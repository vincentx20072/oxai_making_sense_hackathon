"""
Jenga web app: ask questions about PDFs, then check the answer's trust.

    pip install -r requirements.txt
    echo ANTHROPIC_API_KEY=... > .env  # git-ignored; read by llm.py
    python app.py                     # http://localhost:5000

Flow
  1. Upload one or more PDFs and write a prompt.  The PDF agent (agent4.py)
     runs in the background; the page shows its steps live.
  2. The run page has three tabs:
       Answer  the final answer; citations are chips linking to the PDF page.
       Alibi   "was it there?": per claim, were the cited lines shown to the
               agent, and do they hold the claim's keywords.
       Jenga   "did it use it?": layer 1 is the static analysis of the log
               (NO extra model calls); layer 2, "Pull the block", reruns the
               same model and reports how often the claim survives with its
               cited lines blanked, to within a 10-point 95% interval (35-40
               runs, 5 at a time), plus three 5-run checks: PDFs unchanged,
               no PDF text, a wrong value planted (replay.measure_claim).
               It is opt-in, per claim, capped at JENGA_MEASURE_MAX_CALLS
               agent runs, and cached under data/runs/<id>/replay/, so a
               second click costs nothing.
               Ends with the Alibi x Jenga 2x2.
       Agent log  the agent's run as a timeline (or raw), with cited lines marked.

Each run is stored under data/runs/<id>/ (PDFs, agent.log, report.json),
and listed in the sidebar.  You can also import an
existing agent log to analyse it.
"""

import collections
import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)
from markupsafe import Markup, escape
from pypdf import PdfReader
from werkzeug.utils import secure_filename

import agent4
import intervals
import jenga
import judge
import llm
import replay
import webview

BASE = Path(__file__).parent
MAX_FILES = 10
MAX_TOTAL_MB = 25
RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# On Vercel (serverless) the code folder is read-only and work done after a
# response can be cut off, so runs live in /tmp and jobs run inside the request.
ON_VERCEL = bool(os.environ.get("VERCEL"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("JENGA_SECRET_KEY") or os.urandom(24),
    MAX_CONTENT_LENGTH=(MAX_TOTAL_MB + 1) * 1024 * 1024,
    DATA_DIR=Path(os.environ.get("JENGA_DATA_DIR")
                  or ("/tmp/jenga" if ON_VERCEL else BASE / "data")),
    # Replaced in tests with a fake client so no API calls are made.
    CLIENT_FACTORY=agent4.make_client,
    SYNC_JOBS=ON_VERCEL or os.environ.get("JENGA_SYNC_JOBS") == "1",
)

_executor = ThreadPoolExecutor(max_workers=2)
_meta_lock = threading.Lock()


def submit(fn, *args):
    """Start a job: in the background normally, or right here in the request when
    SYNC_JOBS is on (serverless hosts may stop work once the response is sent)."""
    if app.config["SYNC_JOBS"]:
        fn(*args)
    else:
        _executor.submit(fn, *args)


def seed_demo_runs():
    """Copy runs bundled with the code (data/runs/, the demo runs only) into a
    separate data folder once, so a fresh serverless instance isn't empty."""
    src, dst = BASE / "data" / "runs", Path(app.config["DATA_DIR"]) / "runs"
    if src.is_dir() and src.resolve() != dst.resolve():
        dst.mkdir(parents=True, exist_ok=True)
        for run in src.iterdir():
            if run.is_dir() and RUN_ID_RE.match(run.name) and not (dst / run.name).exists():
                shutil.copytree(run, dst / run.name)


# ============================================================
# Storage
# ============================================================

def runs_dir():
    d = Path(app.config["DATA_DIR"]) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_path(run_id):
    if not RUN_ID_RE.match(run_id or ""):
        abort(404)
    p = runs_dir() / run_id
    if not (p / "meta.json").is_file():
        abort(404)
    return p


def load_meta(run_id):
    return json.loads((run_path(run_id) / "meta.json").read_text(encoding="utf-8"))


def save_meta(run_id, **changes):
    with _meta_lock:
        p = runs_dir() / run_id / "meta.json"
        meta = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        meta.update(changes)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return meta


def all_runs():
    out = []
    for p in runs_dir().iterdir():
        try:
            out.append(json.loads((p / "meta.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get("created", ""), reverse=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id():
    return uuid.uuid4().hex[:12]


# ============================================================
# PDF validation
# ============================================================

def validate_pdf(path):
    """Return (pages, has_text) or raise ValueError with a readable message."""
    with open(path, "rb") as f:
        if not f.read(1024).lstrip().startswith(b"%PDF-"):
            raise ValueError("is not a PDF file")
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("is password-protected")
        pages = len(reader.pages)
        has_text = any((pg.extract_text() or "").strip() for pg in reader.pages[:5])
    except ValueError:
        raise
    except Exception:
        raise ValueError("could not be read as a PDF")
    if pages == 0:
        raise ValueError("has no pages")
    return pages, has_text


# ============================================================
# Jobs
# ============================================================

def run_agent_job(run_id):
    d = runs_dir() / run_id
    meta = save_meta(run_id, status="running", started=now_iso())
    t0 = time.time()
    try:
        client = app.config["CLIENT_FACTORY"]()
        out = agent4.run_agent(meta["question"], files_dir=d / "files",
                               log_file=d / "agent.log", runs_dir=d / "trace",
                               run_id=run_id, client=client)
        save_meta(run_id, status="done", finished=now_iso(),
                  duration=round(time.time() - t0, 1),
                  usage=out.get("usage"), cost_usd=out.get("cost_usd"))
    except Exception as e:  # surface the error on the run page
        save_meta(run_id, status="error", finished=now_iso(),
                  duration=round(time.time() - t0, 1),
                  error=f"{type(e).__name__}: {e}"[:800])
        return
    if meta.get("auto_jenga"):
        # The agent succeeded; a failed check must not mark the run failed.
        try:
            analyse_run(run_id)
        except Exception as e:
            flash_note = f"Automatic check failed ({type(e).__name__}: {e}); use 'Run the check'."
            save_meta(run_id, analysis_error=flash_note[:500])
            return
        run_judge_job(run_id, client)


def analyse_run(run_id):
    """Static Jenga analysis of the run's log. No model calls."""
    d = runs_dir() / run_id
    text = (d / "agent.log").read_text(encoding="utf-8")
    reports = jenga.analyse_log_text(text)
    if not reports:
        raise ValueError("The log has no completed answer to analyse.")
    report = reports[-1]
    (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return save_meta(run_id, analysed=True, analysis_error=None, trust_score=report["trust_score"],
                     verdict_counts=report["verdict_counts"],
                     n_claims=len(report["claims"]))


def judge_path(run_id):
    return run_path(run_id) / "judge.json"


def run_judge_job(run_id, client=None):
    """Haiku judge over the analysed claims (one API call, ~$0.005).  Needs
    the Claude API; with any other client it records 'unavailable'."""
    d = run_path(run_id)
    try:
        client = client or app.config["CLIENT_FACTORY"]()
        if not hasattr(client, "messages"):
            save_meta(run_id, judge_status="unavailable")
            return
        save_meta(run_id, judge_status="running", judge_error=None)
        run = webview.latest_run((d / "agent.log").read_text(encoding="utf-8"),
                                 include_incomplete=False)
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        result = judge.judge_run(run, report, client)
        _write_json(judge_path(run_id), result)
        save_meta(run_id, judge_status="done", judge_cost_usd=result["cost_usd"])
    except Exception as e:  # shown on the Alibi tab
        save_meta(run_id, judge_status="error", judge_error=f"{type(e).__name__}: {e}"[:500])


def load_judge(run_id, report):
    """judge.json if it was made for these exact claims, else None."""
    data = _read_json(judge_path(run_id)) if judge_path(run_id).exists() else None
    if not (report and judge.is_current(data, report)):
        return None
    judge.rescore(data)   # older files used more verdict names; recount to the four
    return data


# ============================================================
# Template helpers
# ============================================================

@app.template_filter("ago")
def ago(iso):
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    s = (datetime.now(timezone.utc) - t).total_seconds()
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return t.strftime("%d %b %Y")


@app.template_filter("band")
def band(score):
    if score is None:
        return "muted"
    return "ok" if score >= 75 else "warn" if score >= 45 else "bad"


@app.template_filter("filesize")
def filesize(n):
    n = n or 0
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


@app.context_processor
def inject_globals():
    return {"history": all_runs(), "model": agent4.MODEL,
            "has_key": llm.has_credentials()
            or app.config["CLIENT_FACTORY"] is not agent4.make_client,
            "key_name": "ANTHROPIC_API_KEY" if llm.provider() == "anthropic" else "GROQ_API_KEY",
            "max_files": MAX_FILES, "max_mb": MAX_TOTAL_MB,
            "verdict_style": jenga.VERDICT_STYLE}


# ============================================================
# Routes
# ============================================================

@app.get("/")
def index():
    reuse = None
    if request.args.get("from"):
        try:
            reuse = load_meta(request.args["from"])
        except Exception:
            reuse = None
    return render_template("index.html", reuse=reuse, active=None)


@app.post("/runs")
def create_run():
    question = (request.form.get("question") or "").strip()
    auto = request.form.get("auto_jenga") == "on"
    reuse_from = request.form.get("reuse_from") or ""
    uploads = [f for f in request.files.getlist("pdfs") if f and f.filename]

    if not question:
        flash("Write a question or prompt for the agent.", "error")
        return redirect(url_for("index", **({"from": reuse_from} if reuse_from else {})))
    if len(question) > 4000:
        flash("The prompt is too long (4,000 characters max).", "error")
        return redirect(url_for("index"))

    run_id = new_run_id()
    d = runs_dir() / run_id
    fdir = d / "files"
    fdir.mkdir(parents=True)
    files, errors, total = [], [], 0

    def add(path, name):
        try:
            pages, has_text = validate_pdf(path)
        except ValueError as e:
            path.unlink(missing_ok=True)
            errors.append(f"{name} {e}.")
            return
        files.append({"name": path.name, "size": path.stat().st_size,
                      "pages": pages, "has_text": has_text})

    if reuse_from and RUN_ID_RE.match(reuse_from) and (runs_dir() / reuse_from / "files").is_dir():
        for src in sorted((runs_dir() / reuse_from / "files").glob("*.pdf")):
            shutil.copy2(src, fdir / src.name)
            add(fdir / src.name, src.name)

    if len(uploads) + len(files) > MAX_FILES:
        errors.append(f"Attach at most {MAX_FILES} PDFs.")
    for up in uploads[:MAX_FILES]:
        name = secure_filename(up.filename) or "document.pdf"
        if not name.lower().endswith(".pdf"):
            errors.append(f"{up.filename}: only PDF files are accepted.")
            continue
        dest = fdir / name
        n = 2
        while dest.exists():
            dest = fdir / f"{dest.stem}-{n}.pdf"
            n += 1
        up.save(dest)
        total += dest.stat().st_size
        add(dest, up.filename)
    if total > MAX_TOTAL_MB * 1024 * 1024:
        errors.append(f"Files total more than {MAX_TOTAL_MB} MB.")

    if errors or not files:
        shutil.rmtree(d, ignore_errors=True)
        for e in errors or ["Attach at least one PDF."]:
            flash(e, "error")
        return redirect(url_for("index", **({"from": reuse_from} if reuse_from else {})))

    save_meta(run_id, id=run_id, source="agent", question=question, created=now_iso(),
              status="queued", files=files, model=agent4.MODEL, auto_jenga=auto,
              analysed=False, trust_score=None)
    submit(run_agent_job, run_id)
    return redirect(url_for("view_run", run_id=run_id))


TABS = ("answer", "alibi", "jenga", "log")
TAB_ALIASES = {"trust": "jenga"}  # old links keep working

# Once the Haiku judge has run, its verdict is the claim's Alibi (the
# keyword check is the fallback before that).  A phantom citation (a line
# never shown to the agent) stays phantom whatever the judge says.
# Summary report row: the facts are in the PDF (supported, miscited) or not.
# Summary report's Alibi axis ("facts found?"): a miscited claim's facts and an
# n/a judgement's facts are all in the PDF
JUDGE_TO_ALIBI = {"supported": "supported", "miscited": "supported", "n/a": "supported"}
# Before the judge has run, the keyword check's result shown as the same four
# verdicts: a missing term, or a cited line the agent never saw, is "not found"
# (the keyword check can't tell miscited or contradicted apart).
KEYWORD_VERDICT = {"supported": "supported", "partly supported": "not found",
                   "unsupported": "not found", "phantom": "not found"}


def load_report(run_id, meta):
    """report.json with cached replay labels merged in, or None.

    Reports written by an older analysis are re-analysed once; the static
    analysis is free (no model calls) and gives the same claim ids."""
    d = run_path(run_id)
    if not (meta.get("analysed") and (d / "report.json").exists()):
        return None
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    if report.get("jenga_version", 1) < jenga.REPORT_VERSION and (d / "agent.log").exists():
        try:
            analyse_run(run_id)
            report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    for c in report.get("claims", []):
        done = cached_replay(run_id, c["id"])
        if done and done.get("causal"):
            c["causal"] = done["causal"]
    return report


def tower(c):
    """A claim's cited lines as Jenga bricks, base first.  Neighbouring lines
    with the same role merge into one brick, so a 5-line citation of which
    one line matters reads as two bricks, not five."""
    bricks = []
    for x in c.get("cited_lines", []):
        b = bricks[-1] if bricks else None
        if b and b["role"] == x["role"] and b["page"] == x["page"] and b["end"] + 1 == x["line"]:
            b["end"] = x["line"]
            b["lines"].append(x)
        else:
            bricks.append({"role": x["role"], "page": x["page"], "start": x["line"],
                           "end": x["line"], "lines": [x]})
    for b in bricks:
        b["label"] = f"L{b['start']}" + (f"–{b['end']}" if b["end"] != b["start"] else "")
        b["snippet"] = b["lines"][0].get("text") or "(never shown)"
        only = sorted({t for x in b["lines"] for t in x.get("unique_terms", [])})
        b["title"] = (f"P{b['page']} {b['label']} · {b['role']}"
                      + (f" (only source of: {', '.join(only)})" if only else "") + "\n"
                      + "\n".join(x.get("text") or "(never shown)" for x in b["lines"]))
    return bricks


def claim_flags(c):
    """Static Jenga signals for one claim, as (band, label, terms) rows."""
    terms = c.get("terms", [])
    bridging = set(c.get("bridging_terms", []))
    prior = [t["term"] for t in terms
             if t["support"] == "not in any source" and t["term"] not in bridging]
    outside = [t["term"] for t in terms
               if t["support"] == "elsewhere in sources" and t["term"] not in bridging]
    rows = [("bad", "Said before seen", c.get("timing_terms", [])),
            ("warn", "Question focus not in citation", sorted(bridging)),
            ("warn", "Not in any PDF (model's own knowledge)", prior),
            ("muted", "Only outside the cited lines", outside)]
    return [r for r in rows if r[2]]


def alibi_grid(claims):
    """The Alibi x Jenga 2x2 as {row: {col: {cls, name, claims}}}."""
    cells = jenga.alibi_2x2(claims)
    return {row: {col: {"cls": jenga.QUADRANTS[(row, col)][0],
                        "name": jenga.QUADRANTS[(row, col)][1],
                        "claims": cells[(row, col)]}
                  for col in ("used", "not used")}
            for row in ("supported", "unsupported")}


@app.get("/runs/<run_id>")
def view_run(run_id):
    meta = load_meta(run_id)
    d = run_path(run_id)
    log_text = (d / "agent.log").read_text(encoding="utf-8") if (d / "agent.log").exists() else ""
    run = webview.latest_run(log_text)
    turns, _ = webview.build_turns(run)
    report = load_report(run_id, meta)
    if report:
        meta = load_meta(run_id)  # load_report may have re-analysed (new trust score)
    tab = request.args.get("tab") or ("jenga" if request.args.get("analysed") else "answer")
    tab = TAB_ALIASES.get(tab, tab)
    if tab not in TABS:
        tab = "answer"
    linkable = {f["name"] for f in meta.get("files", [])}
    answer_html = (webview.render_answer(run["answer"], run, run_id, linkable)
                   if run and run["answer"] else "")
    n_calls = sum(1 for t in turns for i in t["items"] if i["type"] in ("call", "select"))

    replays, stories, estimates, grid, n_supported, n_checked, judged = {}, {}, {}, None, 0, 0, None
    if report:
        for c in report["claims"]:
            c["flags"] = claim_flags(c)
            c["tower"] = tower(c)
            c["used"], c["used_source"] = jenga.jenga_use(c)
        judged = load_judge(run_id, report)
        if judged:
            if "ci" not in judged.get("totals", {}):   # judged before intervals existed
                judge.with_interval(judged.setdefault("totals", {}))
            for c in report["claims"]:
                c["judge"] = judged["claims"].get(c["id"])
                if c["judge"]:
                    c["alibi"] = JUDGE_TO_ALIBI.get(c["judge"]["verdict"], "unsupported")
        for c in report["claims"]:   # the verdict the Alibi tab shows
            c["verdict4"] = (c["judge"]["verdict"] if c.get("judge")
                             else KEYWORD_VERDICT.get(c.get("alibi"), "not found"))
        # the tab's score counts what the Alibi tab calls "supported": a miscited
        # claim's facts are in the PDF (the 2x2 grid counts it as found), but not
        # in the lines it cites, so it doesn't count here.  n/a judgements are left
        # out of both sides: the citation question doesn't apply to them.
        n_supported = sum(1 for c in report["claims"] if c["verdict4"] == "supported")
        n_checked = sum(1 for c in report["claims"] if c["verdict4"] != "n/a")
        grid = alibi_grid(report["claims"])
        if tab == "jenga":
            estimates = replay_estimates(report, run_id)
            for c in report["claims"]:
                state = replay_state(run_id, c["id"])
                replays[c["id"]] = state["status"]
                if state["status"] == "done":
                    stories[c["id"]] = replay_story(c, state)
    return render_template(
        "run.html", meta=meta, run=run, turns=turns, report=report, tab=tab,
        replays=replays, stories=stories, estimates=estimates, max_replay_calls=replay.measure_max_calls(),
        grid=grid, n_supported=n_supported, n_checked=n_checked, judge_style=judge.STYLE, verdicts=judge.VERDICTS,
        alibi_style=jenga.ALIBI_STYLE, judged=judged,
        judge_status=meta.get("judge_status"), judge_model=llm.judge_model(),
        answer_html=answer_html, log_text=log_text, n_calls=n_calls,
        steps=webview.progress(run), thought=webview.last_thought(run), active=run_id)


@app.get("/api/runs/<run_id>")
def run_status(run_id):
    meta = load_meta(run_id)
    log = run_path(run_id) / "agent.log"
    run = webview.latest_run(log.read_text(encoding="utf-8")) if log.exists() else None
    return jsonify({"status": meta["status"], "error": meta.get("error"),
                    "steps": webview.progress(run), "thought": webview.last_thought(run),
                    "analysed": meta.get("analysed", False),
                    "judge_status": meta.get("judge_status")})


@app.post("/runs/<run_id>/judge")
def start_judge(run_id):
    meta = load_meta(run_id)
    if not meta.get("analysed"):
        flash("Run the check first.", "error")
    elif meta.get("judge_status") != "running":
        save_meta(run_id, judge_status="running", judge_error=None)
        submit(run_judge_job, run_id)
    return redirect(url_for("view_run", run_id=run_id, tab="alibi"))


@app.post("/runs/<run_id>/analyse")
def analyse(run_id):
    meta = load_meta(run_id)
    if meta["status"] != "done":
        flash("The agent has not finished yet.", "error")
        return redirect(url_for("view_run", run_id=run_id))
    try:
        analyse_run(run_id)
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("view_run", run_id=run_id, tab="jenga"))


@app.get("/runs/<run_id>/agent.log")
def raw_log(run_id):
    return send_from_directory(run_path(run_id), "agent.log", mimetype="text/plain",
                               as_attachment=request.args.get("download") == "1",
                               download_name=f"agent-{run_id}.log")


@app.get("/runs/<run_id>/files/<path:name>")
def run_file(run_id, name):
    return send_from_directory(run_path(run_id) / "files", secure_filename(name),
                               mimetype="application/pdf")


@app.post("/runs/<run_id>/delete")
def delete_run(run_id):
    meta = load_meta(run_id)
    if meta["status"] in ("queued", "running"):
        flash("Wait for the run to finish before deleting it.", "error")
        return redirect(url_for("view_run", run_id=run_id))
    shutil.rmtree(run_path(run_id), ignore_errors=True)
    flash("Run deleted.", "info")
    return redirect(url_for("index"))


@app.route("/import", methods=["GET", "POST"])
def import_log():
    if request.method == "GET":
        return render_template("import.html", active="import")
    f = request.files.get("log")
    text = f.read().decode("utf-8", errors="replace") if f else ""
    runs = jenga.parse_log(text)
    if not runs:
        flash("No completed agent run (NEW QUESTION ... FINAL ANSWER) found in that log.", "error")
        return redirect(url_for("import_log"))
    run_id = new_run_id()
    d = runs_dir() / run_id
    d.mkdir(parents=True)
    (d / "agent.log").write_text(text, encoding="utf-8")
    last = runs[-1]
    save_meta(run_id, id=run_id, source="import", question=last["question"],
              created=now_iso(), status="done", files=[],
              model=last["model"] or "unknown", analysed=False, trust_score=None,
              import_name=secure_filename(f.filename or "agent.log"))
    analyse_run(run_id)
    return redirect(url_for("view_run", run_id=run_id, tab="jenga"))


@app.post("/api/analyse")
def api_analyse():
    """JSON API: upload log file(s) as 'logs', get Jenga reports back."""
    reports = []
    for f in request.files.getlist("logs"):
        reports += jenga.analyse_log_text(f.read().decode("utf-8", errors="replace"))
    if not reports:
        return jsonify({"error": "no completed runs found"}), 400
    return jsonify(reports)


# ============================================================
# Per-claim replay ("Pull the block")
# ============================================================

CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
REPLAY_MODES = ("remove_all", "remove_each", "plant")
DEFAULT_REPLAY_MODES = ["remove_all", "plant"]
_replay_lock = threading.RLock()  # guards _replay_active and the replay files
_replay_active = set()          # (run_id, claim_id) jobs running in this process

# Disk layout under data/runs/<id>/replay/:
#   <claim_id>.json      the finished ReplayResult (the cache; only status "done")
#   <claim_id>.job.json  progress/errors of the latest job (running | error)
# Keeping job state out of the cache file means replay_claim's own cache never
# sees a half-written "running" record, and a failed job never poisons the cache.


def replay_path(run_id, claim_id):
    return run_path(run_id) / "replay" / f"{claim_id}.json"


def replay_job_path(run_id, claim_id):
    return run_path(run_id) / "replay" / f"{claim_id}.job.json"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cached_replay(run_id, claim_id):
    """The finished ReplayResult for this claim, or None."""
    data = _read_json(replay_path(run_id, claim_id))
    if not data or data.get("status") != "done":
        return None
    if data.get("method") == "measured" and data.get("design") != replay.MEASURE_DESIGN:
        return None   # measured with an older method: offer to measure again
    return data


# What the agent did in a run with the claim's cited lines blanked (replay.removal_outcome).
OUTCOME_WORDS = {
    "drops": "the claim was gone",
    "re-cited elsewhere": "the claim came back, citing other lines that say the same thing",
    "cites removed lines": "the claim came back, still citing the blanked lines",
    "survives uncited": "the claim came back with no citation",
}


def _line_ranges(cited_lines):
    """[{page: 1, line: 39}, {1, 40}, {1, 41}] -> "L39–41" (with pages when there are several)."""
    by_page = {}
    for x in cited_lines:
        by_page.setdefault(x["page"], set()).add(x["line"])
    out = []
    for page, nums in sorted(by_page.items()):
        nums, runs = sorted(nums), []
        for n in nums:
            if runs and n == runs[-1][1] + 1:
                runs[-1][1] = n
            else:
                runs.append([n, n])
        out += [(f"P{page}-" if len(by_page) > 1 else "") + f"L{a}" + (f"–{b}" if b != a else "")
                for a, b in runs]
    return ", ".join(out)


def _marked(text, term):
    """An answer, HTML-escaped, with the test's value in <mark>.  Whole words and
    numbers only: a planted "4" must not light up inside "L41" or "2024"."""
    text = text or "(empty answer)"
    if not term:
        return escape(text)
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])")
    out, last = [], 0
    for m in pat.finditer(text):
        out += [escape(text[last:m.start()]), Markup('<mark class="rp-mark">'),
                escape(m.group()), Markup("</mark>")]
        last = m.end()
    out.append(escape(text[last:]))
    return Markup("").join(out)


def _numline(k, n, what, zones, ci=None):
    """One test's number line: the rate k/n as a dot, its 95% Wilson interval
    shaded, and the test's zones [(name, from %, to %)] labelled underneath."""
    lo, hi = ci or intervals.wilson(k, n)
    return {"k": k, "n": n, "what": what, "p": 100 * k / n, "lo": 100 * lo, "hi": 100 * hi,
            "zones": zones, "ticks": [b for _, _, b in zones if b < 100]}


def _sentence(text):
    """'… in 34 of 40 runs (95% CI 71%–93%)' -> one pair of brackets, capitalised, full stop."""
    text = re.sub(r"\((\d+ of \d+ runs) \(95% CI ([^)]*)\)\)", r"(\1, 95% CI \2)", text)
    return text[:1].upper() + text[1:] + "."


def replay_story(c, result):
    """A measured replay for the page: the overall result in one line, then one
    card per test (a paragraph, a number line, an example answer).

    The tests: the claim's cited lines taken out (35-40 runs, the measurement),
    nothing changed (the control), all PDF text taken out, and a value changed.
    The last three run 5 times each, so their intervals are wide."""
    causal = result.get("causal") or {}
    st, label = causal.get("stats") or {}, causal.get("label") or "unclear"
    story = {"label": label, "tests": [],
             "headline": _sentence(causal.get("reason") or "no result was recorded")}
    outs = collections.Counter(causal.get("remove_all_outcomes") or [])
    if not st or not outs:   # an older kind of replay: its one-line reason is all we have
        return story
    after, plant = result.get("after_answers") or {}, causal.get("plant") or {}
    orig, new = plant.get("original"), plant.get("planted")
    tests = story["tests"]

    # 1. the cited lines taken out: the measurement itself
    lines, total = c.get("cited_lines", []), sum(outs.values())
    surv = st.get("survive") or {}
    k, n = surv.get("k", total - outs.get("drops", 0)), surv.get("n", total)
    lo, hi = surv.get("ci") or intervals.wilson(k, n)
    parts = outs.most_common()
    if len(parts) == 1:
        happened = f"Every time, {OUTCOME_WORDS.get(parts[0][0], parts[0][0])}."
    else:
        (o, k1), rest = parts[0], parts[1:]
        tail = "; ".join(f"in {'the other ' if len(rest) == 1 else ''}{k2}, {OUTCOME_WORDS.get(o2, o2)}"
                         for o2, k2 in rest)
        happened = f"In {k1} of {total} runs {OUTCOME_WORDS.get(o, o)}; {tail}."
    recited = st.get("recited", 0)
    redundant = k and lo >= replay.FAITHFUL_BELOW and recited * 2 >= k and not st.get("cites_removed")
    reading = ("It still needs evidence, but the same fact is in other lines too." if redundant
               else "So the claim needs these lines." if hi < replay.FAITHFUL_BELOW
               else "So the claim stands without these lines." if lo > 0.5
               else "So the claim often stands without these lines." if lo >= replay.FAITHFUL_BELOW
               else "That sits on the 20% bar, so it isn't clear either way.")
    tests.append({
        "title": f"Cited lines taken out ({_line_ranges(lines)})",
        "text": (f"We blanked the {len(lines)} line{'s' if len(lines) != 1 else ''} this claim "
                 f"cites and asked the same question {total} times. {happened} {reading}"),
        "line": _numline(k, n, "The claim came back",
                         [("faithful", 0, 20), ("partly", 20, 50), ("post-rationalised", 50, 100)], (lo, hi)),
        "note": (f"The dot is above 20%, but in {recited} of the {k} runs that kept the claim it "
                 "cited other lines saying the same thing, so it counts as redundant evidence, "
                 "not post-rationalisation." if redundant else ""),
        "example": _marked(after.get("remove_all"), orig)})

    # 2. nothing changed: the control
    full = st.get("full") or {}
    if full.get("n"):
        k, n = full["k"], full["n"]
        reading = ("so it makes this claim reliably" if k == n
                   else "so it usually makes this claim" if 2 * k >= n
                   else "so it doesn't make this claim reliably, and the other tests can't be read")
        tests.append({
            "title": "Nothing changed",
            "text": (f"We asked the same question with the PDF untouched (the original answer counts "
                     f"as one of these runs). The agent made the claim in {k} of {n} runs, {reading}."),
            "line": _numline(k, n, "Made the claim", [("unstable", 0, 50), ("stable", 50, 100)]),
            "example": _marked(result.get("before_answer"), orig)})

    # 3. all PDF text taken out: can the model make the claim from memory?
    closed = st.get("closed") or {}
    if closed.get("n"):
        k, n = closed["k"], closed["n"]
        reading = ("so it can't come from memory" if k == 0
                   else "so the model can make it from memory, and the citation may be decoration"
                   if 2 * k > n else "so it sometimes comes from memory")
        tests.append({
            "title": "All PDF text taken out",
            "text": (f"We blanked every line of the PDF and asked again. The agent made the claim "
                     f"anyway in {k} of {n} runs, {reading}."),
            "line": _numline(k, n, "Made the claim anyway", [("from the PDF", 0, 50), ("from memory", 50, 100)]),
            "example": _marked(after.get("closed"), orig)})

    # 4. a value changed: does the answer follow the document or its own belief?
    mem = st.get("memorise") or {}
    if orig and new and mem.get("n"):
        n, kept, followed = mem["n"], mem["k"], mem.get("followed") or 0
        left = n - kept - followed
        text = (f"We changed “{orig}” to “{new}” in the PDF and asked again. "
                f"The answer used {new} in {followed} of {n} runs")
        if kept:
            text += f", kept {orig} in {kept}"
        if left > 0:
            text += f"{',' if kept else ''} and left the claim out in {left}"
        text += (", so it ignores the PDF's value." if 2 * kept > n
                 else ", so it reads the value from the PDF." if 2 * followed > n else ".")
        tests.append({
            "title": f"A value changed ({orig} → {new})", "text": text,
            "line": _numline(kept, n, "Kept its own value", [("follows the PDF", 0, 50), ("ignores the PDF", 50, 100)]),
            "example": _marked(after.get("plant"), new)})
    else:
        tests.append({"title": "A value changed", "line": None, "example": None,
                      "text": "This claim has no number or name we could swap for a wrong one, "
                              "so this test wasn't run."})
    return story


def replay_state(run_id, claim_id):
    """Current state for the UI: cached result, live job, or {'status': 'none'}."""
    with _replay_lock:  # a job finishing mid-read could look "interrupted"
        done = cached_replay(run_id, claim_id)
        if done:
            return {**done, "cached": True}
        job = _read_json(replay_job_path(run_id, claim_id))
        if job:
            if job.get("status") == "running" and (run_id, claim_id) not in _replay_active:
                # The server restarted mid-replay: the job is gone, let the user retry.
                job = {**job, "status": "error",
                       "error": "The replay was interrupted (server restarted). Try again."}
            return job
        return {"run_id": run_id, "claim_id": claim_id, "status": "none"}


def replay_estimates(report, run_id):
    """{claim_id: (usual, worst case) agent runs a measurement would make}.
    The first claim measured also pays for the run's shared baselines."""
    d = runs_dir() / run_id
    return {c["id"]: replay.measure_estimate(d, report, c["id"])
            for c in report.get("claims", [])}


def run_replay_job(run_id, claim_id, modes=None, plant_override=None):
    d = runs_dir() / run_id / "replay"
    job_path = d / f"{claim_id}.job.json"
    result, error = None, None
    try:
        # sampled: k=5 runs per condition (10 if they disagree), 95% intervals
        result = replay.measure_claim(run_id, claim_id, runs_root=runs_dir())
        if result.get("status") != "done":
            raise RuntimeError(result.get("error") or f"replay ended with status {result.get('status')}")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:800]
    with _replay_lock:
        try:
            if error is None:
                _write_json(d / f"{claim_id}.json", {**result, "cached": False})
                job_path.unlink(missing_ok=True)
            else:
                _write_json(job_path, {"run_id": run_id, "claim_id": claim_id,
                                       "status": "error", "error": error})
        finally:
            _replay_active.discard((run_id, claim_id))


@app.post("/runs/<run_id>/replay")
def start_replay(run_id):
    """Pull the block: start (or reuse) a capped, cached replay of one claim."""
    d = run_path(run_id)
    body = request.get_json(silent=True) or {}
    claim_id = str(body.get("claim_id") or "")
    if not CLAIM_ID_RE.match(claim_id):
        return jsonify({"error": "claim_id is required"}), 400
    if not (d / "report.json").exists():
        return jsonify({"error": "Run the Jenga analysis on this run first."}), 409
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    if not any(c["id"] == claim_id for c in report.get("claims", [])):
        return jsonify({"error": f"No claim {claim_id} in this report."}), 404

    cached = cached_replay(run_id, claim_id)
    if cached:  # served from disk: no API calls
        return jsonify({**cached, "cached": True, "calls_used": 0})

    modes = list(DEFAULT_REPLAY_MODES)
    estimate, worst = replay.measure_estimate(d, report, claim_id)
    cap = replay.measure_max_calls()
    if estimate == 0:
        return jsonify({"error": "This claim cites no lines, or has no names or numbers to "
                                 "track across reruns, so there is no block to pull."}), 400
    if worst > cap:
        return jsonify({"error": f"This measurement can need {worst} agent runs; the cap is "
                                 f"{cap} (JENGA_MEASURE_MAX_CALLS)."}), 400

    with _replay_lock:
        key = (run_id, claim_id)
        if key in _replay_active:  # already running: don't start a second job
            return jsonify(replay_state(run_id, claim_id)), 202
        _replay_active.add(key)
        pending = {"run_id": run_id, "claim_id": claim_id, "status": "running",
                   "calls_estimated": estimate, "max_calls": cap, "modes": modes,
                   "started": now_iso()}
        _write_json(replay_job_path(run_id, claim_id), pending)
    submit(run_replay_job, run_id, claim_id)
    if app.config["SYNC_JOBS"]:   # already finished (or failed) inside this request
        return jsonify(replay_state(run_id, claim_id))
    return jsonify(pending), 202


@app.get("/api/runs/<run_id>/replay/<claim_id>")
def replay_status(run_id, claim_id):
    if not CLAIM_ID_RE.match(claim_id):
        abort(404)
    run_path(run_id)
    state = replay_state(run_id, claim_id)
    return jsonify(state), (404 if state["status"] == "none" else 200)


@app.errorhandler(413)
def too_large(_):
    flash(f"The upload is larger than {MAX_TOTAL_MB} MB.", "error")
    return redirect(url_for("index"))


def recover_interrupted():
    """Jobs run in this process, so at start-up nothing is running: a run or
    judge still marked running was cut off by a restart.  Say so instead of
    showing a spinner forever."""
    for m in all_runs():
        if m.get("status") in ("queued", "running"):
            save_meta(m["id"], status="error", finished=now_iso(),
                      error="Interrupted: the server restarted while the agent was running. "
                            "Use 'Ask again with these files' to rerun it.")
        if m.get("judge_status") == "running":
            save_meta(m["id"], judge_status="error",
                      judge_error="Interrupted by a server restart. Check again.")


if ON_VERCEL:
    seed_demo_runs()

if __name__ == "__main__":
    recover_interrupted()
    app.run(debug=False, threaded=True)
