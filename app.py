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
               agent with the cited lines blanked and the claim's value
               planted (replay.replay_claim).  It is opt-in, per claim, capped
               at JENGA_REPLAY_MAX_CALLS agent runs, and cached under
               data/runs/<id>/replay/, so a second click costs nothing.
               Ends with the Alibi x Jenga 2x2.
       Agent log  the agent's run as a timeline (or raw), with cited lines marked.

Each run is stored under data/runs/<id>/ (PDFs, agent.log, report.json,
the infographic), and listed in the sidebar.  You can also import an
existing agent log to analyse it.
"""

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
from pypdf import PdfReader
from werkzeug.utils import secure_filename

import agent4
import jenga
import judge
import llm
import replay
import webview

BASE = Path(__file__).parent
MAX_FILES = 10
MAX_TOTAL_MB = 25
RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("JENGA_SECRET_KEY") or os.urandom(24),
    MAX_CONTENT_LENGTH=(MAX_TOTAL_MB + 1) * 1024 * 1024,
    DATA_DIR=Path(os.environ.get("JENGA_DATA_DIR", BASE / "data")),
    # Replaced in tests with a fake client so no API calls are made.
    CLIENT_FACTORY=agent4.make_client,
)

_executor = ThreadPoolExecutor(max_workers=2)
_meta_lock = threading.Lock()


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
    (d / "jenga.html").write_text(jenga.render_html(report, "Jenga traceability report"),
                                  encoding="utf-8")
    save_meta(run_id, analysed=True, analysis_error=None, trust_score=report["trust_score"],
              verdict_counts=report["verdict_counts"], n_jenga=report["n_jenga"],
              n_claims=len(report["claims"]))
    return update_scores(run_id)   # a still-current judge result overrides the keyword Alibi


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
        update_scores(run_id)   # the judge decides which claims Jenga tests
    except Exception as e:  # shown on the Alibi tab
        save_meta(run_id, judge_status="error", judge_error=f"{type(e).__name__}: {e}"[:500])


def load_judge(run_id, report):
    """judge.json if it was made for these exact claims, else None."""
    data = _read_json(judge_path(run_id)) if judge_path(run_id).exists() else None
    return data if report and judge.is_current(data, report) else None


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
    _executor.submit(run_agent_job, run_id)
    return redirect(url_for("view_run", run_id=run_id))


TABS = ("answer", "alibi", "jenga", "log")
TAB_ALIASES = {"trust": "jenga"}  # old links keep working

# Once the Haiku judge has run, its verdict is the claim's Alibi (the
# keyword check is the fallback before that).  A phantom citation (a line
# never shown to the agent) stays phantom whatever the judge says.
JUDGE_TO_ALIBI = {"supported": "supported", "supported, miscited": "supported",
                  "grounded judgement": "supported",
                  "partly supported": "partly supported",
                  "partly grounded judgement": "partly supported"}


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
    """The Summary report as {row: {col: {cls, name, claims}}}: claims that
    passed Alibi split by Jenga (used / not used); the rest in one cell."""
    cells = jenga.alibi_2x2(claims)
    return {row: {col: {"cls": jenga.QUADRANTS[(row, col)][0],
                        "name": jenga.QUADRANTS[(row, col)][1],
                        "claims": cells[(row, col)]}
                  for (r, col) in jenga.QUADRANTS if r == row}
            for row in ("supported", "unsupported")}


def apply_judge(run_id, report):
    """Attach the judge's result to each claim and make its verdict the
    claim's Alibi (a phantom citation stays phantom).  Returns judge.json, or
    None when there is no judge result for these exact claims."""
    judged = load_judge(run_id, report)
    if judged:
        for c in report["claims"]:
            c["judge"] = judged["claims"].get(c["id"])
            if c["judge"] and c.get("alibi") != "phantom":
                c["alibi"] = JUDGE_TO_ALIBI.get(c["judge"]["verdict"], "unsupported")
    return judged


def update_scores(run_id):
    """Store the Jenga-stage score (over claims that passed Alibi) in meta,
    so the sidebar and tab badge match the page."""
    report = load_report(run_id, load_meta(run_id))
    if not report:
        return load_meta(run_id)
    apply_judge(run_id, report)
    stage = jenga.jenga_stage(report["claims"])
    return save_meta(run_id, trust_score=stage["score"], verdict_counts=stage["verdict_counts"],
                     n_jenga=len(stage["passed"]), n_claims=len(report["claims"]))


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

    replays, estimates, grid, n_supported, judged, stage = {}, {}, None, 0, None, None
    if report:
        for c in report["claims"]:
            c["flags"] = claim_flags(c)
            c["tower"] = tower(c)
            c["used"], c["used_source"] = jenga.jenga_use(c)
        judged = apply_judge(run_id, report)
        stage = jenga.jenga_stage(report["claims"])
        if meta.get("trust_score") != stage["score"]:   # e.g. the judge finished since
            meta = save_meta(run_id, trust_score=stage["score"],
                             verdict_counts=stage["verdict_counts"], n_jenga=len(stage["passed"]))
        n_supported = sum(1 for c in report["claims"] if jenga.passes_alibi(c))
        grid = alibi_grid(report["claims"])
        if tab == "jenga":
            estimates = replay_estimates({**report, "claims": stage["passed"]})
            replays = {c["id"]: replay_state(run_id, c["id"])["status"] for c in stage["passed"]}
    return render_template(
        "run.html", meta=meta, run=run, turns=turns, report=report, tab=tab,
        replays=replays, estimates=estimates, max_replay_calls=replay.max_calls_default(),
        grid=grid, n_supported=n_supported, judge_style=judge.STYLE, stage=stage,
        not_tested_reason=jenga.not_tested_reason,
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
        _executor.submit(run_judge_job, run_id)
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


@app.get("/runs/<run_id>/jenga.html")
def jenga_report(run_id):
    d = run_path(run_id)
    if not (d / "report.json").exists():
        abort(404)
    if request.args.get("embed") == "1":   # embedded view: no duplicate header
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        return jenga.render_html(report, "Jenga traceability report", embedded=True)
    return (d / "jenga.html").read_text(encoding="utf-8")


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
    return data if data and data.get("status") == "done" else None


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


def replay_estimates(report):
    """{claim_id: agent runs a default replay would make} for the Jenga tab."""
    return {c["id"]: replay.estimate_calls(report, c["id"], DEFAULT_REPLAY_MODES)
            for c in report.get("claims", [])}


def run_replay_job(run_id, claim_id, modes, plant_override):
    d = runs_dir() / run_id / "replay"
    job_path = d / f"{claim_id}.job.json"
    result, error = None, None
    try:
        result = replay.replay_claim(run_id, claim_id, modes=modes,
                                     plant_override=plant_override, runs_root=runs_dir())
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
    claim = next((c for c in report.get("claims", []) if c["id"] == claim_id), None)
    if claim is None:
        return jsonify({"error": f"No claim {claim_id} in this report."}), 404
    apply_judge(run_id, report)
    if not jenga.jenga_testable(claim):
        return jsonify({"error": "Jenga only tests cited claims that passed Alibi."}), 400

    cached = cached_replay(run_id, claim_id)
    if cached:  # served from disk: no API calls
        return jsonify({**cached, "cached": True, "calls_used": 0})

    modes = [m for m in (body.get("modes") or DEFAULT_REPLAY_MODES)
             if m in REPLAY_MODES] or list(DEFAULT_REPLAY_MODES)
    plant_override = str(body.get("plant_override") or "").strip()[:200] or None
    estimate = replay.estimate_calls(report, claim_id, modes)
    cap = replay.max_calls_default()
    if estimate == 0:
        return jsonify({"error": "This claim cites no lines, so there is no block to pull."}), 400
    if estimate > cap:
        return jsonify({"error": f"This replay needs {estimate} agent runs; the cap is {cap} "
                                 "(JENGA_REPLAY_MAX_CALLS)."}), 400

    with _replay_lock:
        key = (run_id, claim_id)
        if key in _replay_active:  # already running: don't start a second job
            return jsonify(replay_state(run_id, claim_id)), 202
        _replay_active.add(key)
        pending = {"run_id": run_id, "claim_id": claim_id, "status": "running",
                   "calls_estimated": estimate, "max_calls": cap, "modes": modes,
                   "started": now_iso()}
        _write_json(replay_job_path(run_id, claim_id), pending)
    _executor.submit(run_replay_job, run_id, claim_id, modes, plant_override)
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


if __name__ == "__main__":
    recover_interrupted()
    app.run(debug=False, threaded=True)
