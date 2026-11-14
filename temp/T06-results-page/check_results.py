import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
import app as webapp
webapp.app.config.update(TESTING=True, DATA_DIR=Path(tempfile.mkdtemp()))
c = webapp.app.test_client()
tmp = Path(tempfile.mkdtemp())

def get(path):
    webapp.app.config["RESULTS_PATH"] = str(path) if path else None
    r = c.get("/results"); assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)

# 1. dry-run output written by run_eval.py
h = get(None)
assert "ci-bar" in h and "ci-whisker" in h and "Dry run" in h and "Per question" in h, "dry-run"
# 2. real-run shape: 12 rows, auroc set, dry_run false
rows = [{"id": f"{'K' if i<6 else 'I'}{i:02d}", "family": "known" if i<6 else "invented", "static_score": 50+i,
         "original_correct": True, "plant_outcome": "follows", "remove_all_outcome": "drops",
         "evidence_insensitive": i % 3 == 0, "label": "faithful"} for i in range(12)]
real = {"model": "openai/gpt-oss-120b", "dry_run": False, "n": 12,
        "overall": {"k": 4, "n": 12, "rate": 1/3, "lo": .14, "hi": .61},
        "by_family": {"known": {"k": 2, "n": 6, "rate": 1/3, "lo": .1, "hi": .7},
                      "invented": {"k": 2, "n": 6, "rate": 1/3, "lo": .1, "hi": .7}},
        "auroc": 0.72, "rows": rows}
(tmp/"real.json").write_text(json.dumps(real)); h = get(tmp/"real.json")
assert "openai/gpt-oss-120b" in h and "AUROC 0.72" in h and "Dry run" not in h and h.count("<code>K0") == 6, "real"
assert "4 / 12" in h and "33%" in h
# 3. partial/in-progress: nulls and missing keys
(tmp/"part.json").write_text(json.dumps({"model": "x", "overall": {"k": 0, "n": 0, "rate": None, "lo": None, "hi": None}}))
h = get(tmp/"part.json"); assert "n/a" in h and "ci-whisker" not in h, "partial"
# 4. invalid JSON -> error flash, no 500
(tmp/"bad.json").write_text("{nope"); h = get(tmp/"bad.json"); assert "Could not read bad.json" in h, "bad"
# 5. missing file -> empty state
h = get(tmp/"missing.json"); assert "No results yet" in h, "missing"
# 6. sidebar link
assert 'href="/results"' in h
print("results page checks: 6/6 passed")
