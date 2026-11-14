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
     of the log: it makes NO extra model calls.
  3b. "Pull the block" on one claim reruns the agent with the cited lines
     blanked and with the claim's value planted (replay.replay_claim).  It is
     opt-in, per claim, capped at JENGA_REPLAY_MAX_CALLS agent runs, and cached
     under data/runs/<id>/replay/, so a second click costs nothing.
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
    replays, estimates = {}, {}
    if report and tab == "trust":
        estimates = replay_estimates(report)
        replays = {c["id"]: replay_state(run_id, c["id"])["status"] for c in report["claims"]}
    return render_template(
        "run.html", meta=meta, run=run, turns=turns, report=report, tab=tab,
        replays=replays, estimates=estimates, max_replay_calls=replay.max_calls_default(),
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
    """{claim_id: agent runs a default replay would make} for the Trust tab."""
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
    if not any(c["id"] == claim_id for c in report.get("claims", [])):
        return jsonify({"error": f"No claim {claim_id} in this report."}), 404

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


@app.get("/results")
def results():
    path = BASE / "eval" / "results.json"
    summary = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return render_template("results.html", summary=summary, active="results")


@app.errorhandler(413)
def too_large(_):
    flash(f"The upload is larger than {MAX_TOTAL_MB} MB.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=False, threaded=True)
