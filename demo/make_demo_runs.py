"""
Pre-create the demo runs so the live demo opens them from History by id.

    python demo/make_demo_pdfs.py
    python demo/make_demo_runs.py                 # both questions, real model (ANTHROPIC_API_KEY)
    python demo/make_demo_runs.py --only known    # one question
    python demo/make_demo_runs.py --replay        # also cache "Pull the block" for the claim
    python demo/make_demo_runs.py --fake          # offline smoke test, scripted agent, no API

For each question in demo/questions.json it creates a run exactly the way the
web app does (data/runs/<id>/{files, meta.json, agent.log, trace, report.json}),
using app.run_agent_job, then checks the answer is "clean": exactly one claim,
it contains the expected value and it cites the fact line. Unclean runs are
deleted and retried (up to --attempts). The kept run ids are printed and,
with --write, recorded in demo/RUNS.md.

Every attempt is one full agent run (a handful of model calls). --replay adds
replay.replay_claim for the claim (capped by --max-calls) so the demo can show
the causal label from cache with 0 API calls.
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

QUESTIONS = HERE / "questions.json"
PDF_DIR = HERE / "pdfs"
RUNS_MD = HERE / "RUNS.md"


def load_questions(only=None):
    qs = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return [q for q in qs if not only or q["id"] in only]


def check_clean(report, q):
    """Return None if the run is demo-ready, else the reason it is not."""
    claims = report.get("claims") or []
    if len(claims) != 1:
        return f"{len(claims)} claims (want 1)"
    c = claims[0]
    if q["value"] not in c["text"]:
        return f"claim does not state {q['value']}: {c['text'][:120]!r}"
    page, line = (int(x) for x in re.match(r"P(\d+)-L(\d+)", q["fact_line"]).groups())
    cited = {(cl.get("page"), cl.get("line")) for cl in c.get("cited_lines") or []}
    if (page, line) not in cited:
        return f"claim does not cite {q['fact_line']} (cites {sorted(cited)})"
    return None


def create_run(app_mod, q, pdf_dir=PDF_DIR):
    """Create and run one agent episode the way POST /runs does; return run id."""
    run_id = app_mod.new_run_id()
    d = app_mod.runs_dir() / run_id
    (d / "files").mkdir(parents=True)
    dest = d / "files" / q["pdf"]
    shutil.copy2(Path(pdf_dir) / q["pdf"], dest)
    pages, has_text = app_mod.validate_pdf(dest)
    app_mod.save_meta(run_id, id=run_id, source="agent", question=q["question"],
                      created=app_mod.now_iso(), status="queued",
                      files=[{"name": dest.name, "size": dest.stat().st_size,
                              "pages": pages, "has_text": has_text}],
                      model=app_mod.agent4.MODEL, auto_jenga=True,
                      analysed=False, trust_score=None, demo=q["id"])
    app_mod.run_agent_job(run_id)        # synchronous; also runs static Jenga
    return run_id


def make_one(app_mod, q, attempts, pdf_dir=PDF_DIR, log=print):
    for n in range(1, attempts + 1):
        run_id = create_run(app_mod, q, pdf_dir)
        d = app_mod.runs_dir() / run_id
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        if meta.get("status") != "done":
            reason = meta.get("error") or meta.get("status")
        else:
            report = json.loads((d / "report.json").read_text(encoding="utf-8"))
            reason = check_clean(report, q)
        if reason is None:
            log(f"[{q['id']}] attempt {n}: clean -> run {run_id}")
            return run_id
        log(f"[{q['id']}] attempt {n}: not clean ({reason}); deleting {run_id}")
        shutil.rmtree(d, ignore_errors=True)
        if meta.get("status") == "error" and "api_key" in str(meta.get("error", "")).lower():
            break
    return None


def cache_replay(app_mod, run_id, q, max_calls, client=None):
    import replay
    res = replay.replay_claim(run_id, "c1", plant_override=q["plant"], samples=1,
                              max_calls=max_calls, client=client,
                              runs_root=app_mod.runs_dir())
    return res


def write_runs_md(rows, path=RUNS_MD):
    lines = [
        "# Demo runs",
        "",
        "Pre-created by `python demo/make_demo_runs.py --write` "
        f"on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "Open them from History, or at `/runs/<id>`. Each id must also be in "
        "`.gitignore` as `!data/runs/<id>/` so the run is committed.",
        "",
        "| Demo | Run id | Question | Fact (line) | Plant | Static verdict | Replay label |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | `{r['run_id']}` | {r['question']} | {r['value']} "
                     f"({r['fact_line']}) | {r['plant']} | {r['verdict']} | {r['label']} |")
    lines += ["", ".gitignore lines:", "", "```"]
    lines += [f"!data/runs/{r['run_id']}/" for r in rows]
    lines += ["```", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="question ids (default: all)")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--replay", action="store_true", help="also cache replay_claim for c1")
    ap.add_argument("--max-calls", type=int, default=12)
    ap.add_argument("--data-dir", help="instead of data/ (JENGA_DATA_DIR)")
    ap.add_argument("--write", action="store_true", help="write demo/RUNS.md")
    ap.add_argument("--fake", action="store_true", help="scripted offline agent (smoke test)")
    a = ap.parse_args(argv)

    import app as app_mod
    if a.data_dir:
        app_mod.app.config["DATA_DIR"] = Path(a.data_dir)
    client = None
    if a.fake:
        from demo.fake_agent import FakeDemoClient
        app_mod.app.config["CLIENT_FACTORY"] = lambda: FakeDemoClient()
        client = FakeDemoClient()

    rows, failed = [], []
    for q in load_questions(a.only):
        if not (PDF_DIR / q["pdf"]).is_file():
            sys.exit(f"missing {PDF_DIR / q['pdf']}: run python demo/make_demo_pdfs.py first")
        run_id = make_one(app_mod, q, a.attempts)
        if not run_id:
            failed.append(q["id"])
            continue
        report = json.loads((app_mod.runs_dir() / run_id / "report.json").read_text(encoding="utf-8"))
        label = "not replayed"
        if a.replay:
            label = cache_replay(app_mod, run_id, q, a.max_calls, client)["causal"]["label"]
        rows.append({**q, "run_id": run_id, "verdict": report["claims"][0]["verdict"],
                     "label": label})
    for r in rows:
        print(f"{r['id']}: {r['run_id']}  verdict={r['verdict']}  replay={r['label']}")
    if a.write and rows:
        write_runs_md(rows)
        print(f"wrote {RUNS_MD}")
    if failed:
        print("no clean run for: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
