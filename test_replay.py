"""
Offline tests for replay_claim, estimate_calls and perturb (no network).

    python test_replay.py
"""

import json
import tempfile
from pathlib import Path

import replay

REPORT = {
    "answer": "Python since 1870 [cv.pdf, P1-L2:L3]",
    "claims": [
        {"id": "C1", "text": "Python since 1870", "terms": [],
         "cited_lines": [{"file": "cv.pdf", "page": 1, "line": 2},
                         {"file": "cv.pdf", "page": 1, "line": 3},
                         {"file": "cv.pdf", "page": 2, "line": 9}]},
        {"id": "C2", "text": "uncited", "terms": [], "cited_lines": []},
    ],
}


def test_estimate_calls():
    assert replay.estimate_calls(REPORT, "C1") == 2
    assert replay.estimate_calls(REPORT, "C1", ("remove_each",)) == 2  # two spans
    assert replay.estimate_calls(REPORT, "C1", samples=3) == 6
    assert replay.estimate_calls(REPORT, "C2") == 0
    assert replay.estimate_calls(REPORT, "nope") == 0


def test_perturb_changes_value():
    for t in ("1870", "42", "python"):
        out = replay.perturb(t, "small")
        assert isinstance(out, str) and out and out != t, (t, out)


def test_replay_claim_stub_shape():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "abcdefabcdef"
        d.mkdir()
        (d / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
        r = replay.replay_claim("abcdefabcdef", "C1", runs_root=tmp)
    for k in ("run_id", "claim_id", "status", "cached", "calls_used",
              "calls_estimated", "before_answer", "after_answers", "causal", "error"):
        assert k in r, k
    assert r["causal"]["label"] in ("faithful", "post-rationalised", "unclear")
    assert r["calls_used"] <= replay.max_calls_default()
    assert r["calls_estimated"] == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
