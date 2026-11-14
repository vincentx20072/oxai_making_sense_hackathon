"""Offline tests for eval/stats.py and run_eval --dry-run.

    python eval/test_eval.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import run_eval  # noqa: E402
from eval.pdfs import write_pdf  # noqa: E402
from eval.stats import auroc, rate_ci, wilson_ci  # noqa: E402


def close(a, b, tol=1e-3):
    return abs(a - b) < tol


def test_wilson():
    lo, hi = wilson_ci(5, 10)
    assert close(lo, 0.2366) and close(hi, 0.7634), (lo, hi)
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0 and close(hi, 0.2775), hi
    assert wilson_ci(0, 0) == (None, None)


def test_auroc():
    assert auroc([3, 2, 1, 0], [1, 1, 0, 0]) == 1.0
    assert auroc([0, 1, 2, 3], [1, 1, 0, 0]) == 0.0
    assert auroc([1, 1], [1, 0]) == 0.5
    assert auroc([1, 2], [1, 1]) is None
    assert auroc([2, 1, 1, 0], [1, 1, 0, 0]) == 0.875  # one tie counts 1/2


def test_rate_ci():
    r = rate_ci(3, 12)
    assert r["k"] == 3 and r["n"] == 12 and close(r["rate"], 0.25)
    assert r["lo"] < 0.25 < r["hi"]
    assert rate_ci(0, 0)["rate"] is None


def test_questions():
    qs = run_eval.load_questions()
    fams = [q["family"] for q in qs]
    assert len(qs) == 12 and fams.count("known") == 6 and fams.count("invented") == 6, fams
    assert len({q["id"] for q in qs}) == 12
    for q in qs:
        assert set(q) == {"id", "family", "question", "pdf", "text", "value", "plant"}, q["id"]
        assert q["value"].lower() not in q["question"].lower(), q["id"]
        assert q["value"] != q["plant"] and q["plant"] not in q["text"], q["id"]
        run_eval.fact_line(q)  # value appears on a line of the PDF text


def test_pdf_roundtrip():
    from pypdf import PdfReader
    q = run_eval.load_questions()[0]
    with tempfile.TemporaryDirectory() as tmp:
        p = write_pdf(Path(tmp) / "x.pdf", q["text"])
        lines = PdfReader(p).pages[0].extract_text().splitlines()
    assert lines == q["text"].split(chr(10)), lines


def test_dry_run():
    with tempfile.TemporaryDirectory() as tmp:
        assert run_eval.main(["--dry-run", "--out", tmp]) == 0
        s = json.loads((Path(tmp) / "results.json").read_text(encoding="utf-8"))
        rows = (Path(tmp) / "results.jsonl").read_text(encoding="utf-8").splitlines()
    n = len(run_eval.load_questions())
    assert s["dry_run"] and s["n"] == n == len(rows)
    for k in ("model", "overall", "by_family", "auroc", "rows"):
        assert k in s, k
    assert set(s["by_family"]) == {"known", "invented"}
    assert s["overall"]["lo"] <= s["overall"]["rate"] <= s["overall"]["hi"]
    # the real jenga + replay code separates the two fake policies
    for r in s["rows"]:
        want = "post-rationalised" if r["family"] == "known" else "faithful"
        assert r["label"] == want, r
        assert r["evidence_insensitive"] == (r["family"] == "known"), r
        assert r["original_correct"], r
    assert s["by_family"]["known"]["rate"] == 1.0 and s["by_family"]["invented"]["rate"] == 0.0
    assert s["auroc"] == 1.0


def test_plants_the_value():
    """Replay must plant the question's value, not some other term of the claim."""
    with tempfile.TemporaryDirectory() as tmp:
        for q in run_eval.load_questions():
            r = run_eval.run_question(q, tmp, dry_run=True)
            assert r["_plant_term"].lower() == q["value"].lower(), (q["id"], r["_plant_term"])
            assert r["_planted"] == q["plant"], (q["id"], r["_planted"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
