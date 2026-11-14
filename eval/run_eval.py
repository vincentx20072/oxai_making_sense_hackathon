"""
Pilot evaluation: original + plant + remove-all per question.

    python eval/run_eval.py --dry-run      # offline, synthetic outcomes, no API calls
    python eval/run_eval.py                # real run (T09)

Writes eval/results.jsonl (one EvalRow per line) and eval/results.json (EvalSummary).
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from eval.stats import auroc, rate_ci  # noqa: E402

QUESTIONS = HERE / "questions.jsonl"


def load_questions(path=QUESTIONS):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def dry_row(q):
    """Deterministic fake outcome: known facts ignore evidence, invented ones follow it."""
    known = q["family"] == "known"
    return {"id": q["id"], "family": q["family"],
            "static_score": 80 if known else 60, "original_correct": True,
            "plant_outcome": "overrides and cites" if known else "follows",
            "remove_all_outcome": "cites removed lines" if known else "drops",
            "evidence_insensitive": known,
            "label": "post-rationalised" if known else "faithful"}


def summarise(rows, model, dry_run):
    def rc(rs):
        return rate_ci(sum(r["evidence_insensitive"] for r in rs), len(rs))
    fams = sorted({r["family"] for r in rows})
    scored = [r for r in rows if r["static_score"] is not None and r["label"] != "unclear"]
    return {"model": model, "dry_run": dry_run, "n": len(rows), "overall": rc(rows),
            "by_family": {f: rc([r for r in rows if r["family"] == f]) for f in fams},
            # positive class = post-rationalised; a useful static score is LOW for those
            "auroc": auroc([-r["static_score"] for r in scored],
                           [r["label"] == "post-rationalised" for r in scored]),
            "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--questions", default=str(QUESTIONS))
    ap.add_argument("--out", default=str(HERE))
    a = ap.parse_args(argv)
    if not a.dry_run:
        print("Real runs are not implemented yet (T09). Use --dry-run.")
        return 2
    rows = [dry_row(q) for q in load_questions(a.questions)]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                       encoding="utf-8")
    summary = summarise(rows, "dry-run", True)
    (out / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote {out / 'results.json'} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
