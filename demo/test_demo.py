"""
Offline checks for the demo dataset (T07). No API key needed.

    python demo/test_demo.py

1. questions.json is well formed, and each fact sits once, on its fact_line.
2. make_demo_pdfs builds PDFs that pypdf (the agent's reader) splits into the
   same lines, and the committed demo/pdfs match the generator.
3. make_demo_runs with the scripted agent creates runs that open from History,
   each with one claim citing the fact line, and planting flips the value.
4. Every run id listed in demo/RUNS.md exists in data/runs/ with one clean claim.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader  # noqa: E402

from demo import make_demo_pdfs, make_demo_runs  # noqa: E402
from demo.fake_agent import FakeDemoClient  # noqa: E402

QS = make_demo_pdfs.load_questions()


def _lines(path):
    return PdfReader(path).pages[0].extract_text().splitlines()


def test_questions_well_formed():
    keys = {"id", "family", "pdf", "question", "value", "plant", "fact_line", "lines"}
    assert {q["id"] for q in QS} == {"known", "invented"}
    for q in QS:
        assert keys <= set(q), q["id"]
        assert q["family"] in ("known", "invented")
        line = int(re.fullmatch(r"P1-L(\d+)", q["fact_line"]).group(1))
        text = "\n".join(q["lines"])
        assert len(re.findall(rf"\b{q['value']}\b", text)) == 1, "value must appear once"
        assert q["value"] in q["lines"][line - 1]
        assert q["plant"] not in text and q["plant"] != q["value"]
        assert abs(int(q["plant"]) - int(q["value"])) <= 4, "plants are small (±2-4 years)"


def test_pdfs_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        for path in make_demo_pdfs.build(tmp):
            q = next(q for q in QS if q["pdf"] == path.name)
            assert _lines(path) == q["lines"], path.name
            committed = HERE / "pdfs" / path.name
            assert committed.is_file(), f"run python demo/make_demo_pdfs.py ({committed})"
            assert _lines(committed) == q["lines"], f"{committed} is stale; rebuild it"


def test_fake_runs_clean_and_open_from_history():
    import agent4
    import app as app_mod
    with tempfile.TemporaryDirectory() as tmp:
        old = dict(app_mod.app.config)
        app_mod.app.config.update(DATA_DIR=Path(tmp), TESTING=True,
                                  CLIENT_FACTORY=lambda: FakeDemoClient())
        try:
            ids = {}
            for q in QS:
                run_id = make_demo_runs.make_one(app_mod, q, attempts=1, log=lambda *_: None)
                assert run_id, q["id"]
                ids[q["id"]] = run_id
            with app_mod.app.test_client() as c:
                home = c.get("/").get_data(as_text=True)
                for q in QS:
                    rid = ids[q["id"]]
                    assert rid in home, "run must be listed in History"
                    page = c.get(f"/runs/{rid}")
                    assert page.status_code == 200
                    report = json.loads((Path(tmp) / "runs" / rid / "report.json")
                                        .read_text(encoding="utf-8"))
                    assert make_demo_runs.check_clean(report, q) is None
                    assert report["claims"][0]["verdict"] == "grounded"
        finally:
            app_mod.app.config.update(old)

    # planting the small wrong value changes what the (honest) fake agent says
    q = QS[0]
    with tempfile.TemporaryDirectory() as tmp:
        out = agent4.run_agent(q["question"], files_dir=HERE / "pdfs",
                               plants={q["value"]: q["plant"]}, client=FakeDemoClient(),
                               log_file=Path(tmp) / "a.log", runs_dir=Path(tmp) / "r")
    assert q["plant"] in out["answer"] and q["value"] not in out["answer"]


def test_runs_md_ids_exist():
    md = HERE / "RUNS.md"
    ids = re.findall(r"\|\s*(known|invented)\s*\|\s*`([0-9a-f]{12})`", md.read_text(encoding="utf-8"))
    for qid, rid in ids:
        d = ROOT / "data" / "runs" / rid
        assert (d / "meta.json").is_file(), f"{rid} listed in RUNS.md but missing"
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        q = next(q for q in QS if q["id"] == qid)
        assert make_demo_runs.check_clean(report, q) is None, rid


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
