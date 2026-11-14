"""
Jenga web app: ask questions about PDFs, then check the answer's trust.

    pip install -r requirements.txt
    export GROQ_API_KEY=...
    python app.py                      # http://localhost:5000

Flow
  1. Upload one or more PDFs and write a prompt.  The PDF agent (agent4.py)
     runs in the background; the page shows its steps live.
  2. Read the answer.  Citations are chips linking to the cited PDF page.
  3. Run Jenga (automatically or with a button).  This is a static analysis
     of the log: it makes NO extra model calls.  Causal replays are
     deliberately not available here because they cost many API calls.
  4. Inspect the agent's log as a formatted timeline, or raw.

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
    # /results reads this EvalSummary; defaults to eval/results.json.
    RESULTS_PATH=os.environ.get("JENGA_RESULTS_PATH") or None,
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
        agent4.run_agent(meta["question"], files_dir=d / "files",
                         log_file=d / "agent.log", runs_dir=d / "trace",
                         run_id=run_id, client=client)
        save_meta(run_id, status="done", finished=now_iso(),
                  duration=round(time.time() - t0, 1))
        if meta.get("auto_jenga"):
            analyse_run(run_id)
    except Exception as e:  # surface the error on the run page
        save_meta(run_id, status="error", finished=now_iso(),
                  duration=round(time.time() - t0, 1),
                  error=f"{type(e).__name__}: {e}"[:800])


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
    (d / "jenga.html").write_text(jenga.render_html(report, "Jenga trust report"),
                                  encoding="utf-8")
    return save_meta(run_id, analysed=True, trust_score=report["trust_score"],
                     verdict_counts=report["verdict_counts"],
                     n_claims=len(report["claims"]))


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
            "has_key": bool(os.environ.get("GROQ_API_KEY"))
            or app.config["CLIENT_FACTORY"] is not agent4.make_client,
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


@app.get("/runs/<run_id>")
def view_run(run_id):
    meta = load_meta(run_id)
    d = run_path(run_id)
    log_text = (d / "agent.log").read_text(encoding="utf-8") if (d / "agent.log").exists() else ""
    run = webview.latest_run(log_text)
    turns, _ = webview.build_turns(run)
    report = None
    if meta.get("analysed") and (d / "report.json").exists():
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    tab = request.args.get("tab") or ("trust" if request.args.get("analysed") else "answer")
    if tab not in ("answer", "trust", "log"):
        tab = "answer"
    linkable = {f["name"] for f in meta.get("files", [])}
    answer_html = (webview.render_answer(run["answer"], run, run_id, linkable)
                   if run and run["answer"] else "")
    n_calls = sum(1 for t in turns for i in t["items"] if i["type"] in ("call", "select"))
    return render_template(
        "run.html", meta=meta, run=run, turns=turns, report=report, tab=tab,
        answer_html=answer_html, log_text=log_text, n_calls=n_calls,
        steps=webview.progress(run), thought=webview.last_thought(run), active=run_id)


@app.get("/api/runs/<run_id>")
def run_status(run_id):
    meta = load_meta(run_id)
    log = run_path(run_id) / "agent.log"
    run = webview.latest_run(log.read_text(encoding="utf-8")) if log.exists() else None
    return jsonify({"status": meta["status"], "error": meta.get("error"),
                    "steps": webview.progress(run), "thought": webview.last_thought(run),
                    "analysed": meta.get("analysed", False)})


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
    return redirect(url_for("view_run", run_id=run_id, tab="trust"))


@app.get("/runs/<run_id>/jenga.html")
def jenga_report(run_id):
    d = run_path(run_id)
    if not (d / "report.json").exists():
        abort(404)
    if request.args.get("embed") == "1":   # inside the Trust tab: no duplicate header
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        return jenga.render_html(report, "Jenga trust report", embedded=True)
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
    return redirect(url_for("view_run", run_id=run_id, tab="trust"))


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
# Per-claim replay ("Pull the block") and pilot results
# ============================================================

CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def replay_path(run_id, claim_id):
    return run_path(run_id) / "replay" / f"{claim_id}.json"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_replay_job(run_id, claim_id, modes, plant_override):
    path = runs_dir() / run_id / "replay" / f"{claim_id}.json"
    try:
        result = replay.replay_claim(run_id, claim_id, modes=modes,
                                     plant_override=plant_override, runs_root=runs_dir())
    except Exception as e:
        result = {"run_id": run_id, "claim_id": claim_id, "status": "error",
                  "error": f"{type(e).__name__}: {e}"[:800]}
    _write_json(path, result)


@app.post("/runs/<run_id>/replay")
def start_replay(run_id):
    d = run_path(run_id)
    body = request.get_json(silent=True) or {}
    claim_id = str(body.get("claim_id") or "")
    if not CLAIM_ID_RE.match(claim_id):
        return jsonify({"error": "claim_id is required"}), 400
    if not (d / "report.json").exists():
        return jsonify({"error": "run Jenga on this run first"}), 409
    modes = [m for m in (body.get("modes") or ["remove_all", "plant"])
             if m in ("remove_all", "remove_each", "plant")] or ["remove_all", "plant"]
    plant_override = body.get("plant_override") or None
    path = replay_path(run_id, claim_id)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("status") == "done":
            return jsonify({**cached, "cached": True, "calls_used": 0})
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    pending = {"run_id": run_id, "claim_id": claim_id, "status": "running",
               "calls_estimated": replay.estimate_calls(report, claim_id, modes)}
    _write_json(path, pending)
    _executor.submit(run_replay_job, run_id, claim_id, modes, plant_override)
    return jsonify(pending), 202


@app.get("/api/runs/<run_id>/replay/<claim_id>")
def replay_status(run_id, claim_id):
    if not CLAIM_ID_RE.match(claim_id):
        abort(404)
    path = replay_path(run_id, claim_id)
    if not path.exists():
        return jsonify({"run_id": run_id, "claim_id": claim_id, "status": "none"}), 404
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


def _num(x):
    """A float in [0, 1], or None for missing / non-numeric values."""
    try:
        return None if x is None else min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return None


def _rate_group(name, r):
    r = r if isinstance(r, dict) else {}
    k, n = r.get("k"), r.get("n")
    rate = _num(r.get("rate"))
    if rate is None and isinstance(k, int) and isinstance(n, int) and n > 0:
        rate = k / n
    return {"name": name, "k": k, "n": n, "rate": rate,
            "lo": _num(r.get("lo")), "hi": _num(r.get("hi"))}


def load_results(path):
    """Read eval/results.json (EvalSummary). Returns (summary, groups, error).

    Tolerates a missing file, invalid JSON and missing keys, so a partial or
    in-progress real run still renders instead of a 500."""
    path = Path(path)
    if not path.exists():
        return None, [], None
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("top level is not an object")
    except (OSError, ValueError) as e:
        return None, [], f"Could not read {path.name}: {e}"
    by_family = summary.get("by_family") if isinstance(summary.get("by_family"), dict) else {}
    groups = [_rate_group("All", summary.get("overall"))]
    groups += [_rate_group(f, by_family[f]) for f in sorted(by_family)]
    rows = summary.get("rows") if isinstance(summary.get("rows"), list) else []
    summary["rows"] = [r for r in rows if isinstance(r, dict)]
    return summary, groups, None


@app.get("/results")
def results():
    path = app.config.get("RESULTS_PATH") or BASE / "eval" / "results.json"
    summary, groups, error = load_results(path)
    return render_template("results.html", summary=summary, groups=groups,
                           error=error, active="results")


@app.errorhandler(413)
def too_large(_):
    flash(f"The upload is larger than {MAX_TOTAL_MB} MB.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=False, threaded=True)
