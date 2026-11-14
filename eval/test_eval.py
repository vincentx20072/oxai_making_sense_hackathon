"""Offline tests for eval/stats.py and run_eval --dry-run.

    python eval/test_eval.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import run_eval  # noqa: E402
from eval.stats import auroc, wilson_ci  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
