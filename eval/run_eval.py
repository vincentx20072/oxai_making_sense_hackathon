"""
Pilot evaluation: original + plant + remove-all per question.

    python eval/run_eval.py --dry-run      # offline: real agent4 + jenga + replay, FakeClient, 0 API calls
    python eval/run_eval.py                # real run on Claude: ~4 agent runs per question
    python eval/run_eval.py --only K01,I01 # subset

For each EvalQuestion we build a one-page PDF from its `text`, run agent4.run_agent on it,
score the answer with jenga (static score), then replay.replay_report with the claim's
value planted to `plant` and with the cited span(s) blanked. Writes eval/results.jsonl
(one EvalRow per line) and eval/results.json (EvalSummary).

Dry-run policy (FakeClient): "known" questions use a *prior* agent that decides the answer
before reading and cites the fact line whatever it says (expected: post-rationalised);
"invented" questions use a *reader* agent that reports what the fact line currently says
(expected: faithful). The labels come from the real replay code, not from the family.
"""

import argparse
import contextlib
import copy
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from eval.pdfs import write_pdf  # noqa: E402
from eval.stats import auroc, rate_ci  # noqa: E402

QUESTIONS = HERE / "questions.jsonl"
INSENSITIVE_PLANT = ("overrides and cites", "overrides without citing")


def load_questions(path=QUESTIONS):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ------------------------------------------------------------------ fake agent (dry run)

def fact_line(q):
    """1-based line number of the sentence holding q['value'] in q['text']."""
    for n, line in enumerate(q["text"].split("\n"), start=1):
        if re.search(rf"(?<![\w+#]){re.escape(q['value'])}(?![\w+#])", line):
            return n
    raise ValueError(f"{q['id']}: value {q['value']!r} not in text")


class FakeClient:
    """Mimics client.chat.completions.create with a scripted two-step policy:
    read page 1, then answer. Never touches the network."""

    def __init__(self, q, prior):
        self.q, self.prior, self.n = q, prior, fact_line(q)
        self.calls = 0
        self.chat = NS(completions=NS(create=self.create))

    @staticmethod
    def _msg(content="", reasoning="", call=None, step=0):
        calls = None
        if call:
            calls = [NS(id=f"call{step}", function=NS(name=call[0], arguments=json.dumps(call[1])))]
        return NS(choices=[NS(message=NS(content=content, reasoning=reasoning, tool_calls=calls))])

    def create(self, model, messages, tools, tool_choice, **kw):
        self.calls += 1
        q, pdf = self.q, self.q["pdf"]
        step = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant")
        cite = f"[{pdf}, P1-L{self.n}]"
        if step == 0:
            why = (f"I already know the answer is {q['value']}; I just need a line to cite."
                   if self.prior else "I should read the document before answering.")
            return self._msg(reasoning=why, call=("read_pdf_pages", {
                "filename": pdf, "start_page": 1, "end_page": 1}), step=step)
        if self.prior:
            original = q["text"].split("\n")[self.n - 1]
            return self._msg(content=f"- {original} {cite}")
        tool_out = next((m["content"] for m in messages
                         if isinstance(m, dict) and m.get("role") == "tool"), "")
        m = re.search(rf"\[P1-L{self.n}\] (.*)", tool_out)
        line = (m.group(1).strip() if m else "")
        if not line:
            return self._msg(content="The document does not answer this question.")
        return self._msg(content=f"- {line} {cite}")


# ------------------------------------------------------------------ one question

def _majority(xs):
    return max(set(xs), key=xs.count) if xs else None


def _main_claim(report, value):
    cited = [c for c in report["claims"] if c["cited_lines"]]
    with_value = [c for c in cited if value.lower() in c["text"].lower()]
    return (with_value or cited or [None])[0]


def run_question(q, work_dir, client=None, dry_run=False, samples=1, quiet=True):
    """Run original + remove-all + plant for one EvalQuestion. Returns an EvalRow
    (plus a few extra diagnostic keys prefixed with '_')."""
    import agent4
    import replay
    from jenga import analyse_log_text

    files_dir = Path(work_dir) / q["id"]
    write_pdf(files_dir / q["pdf"], q["text"])
    if dry_run:
        client = FakeClient(q, prior=q["family"] == "known")
    log_file, runs_dir = Path(work_dir) / "eval.log", Path(work_dir) / "runs"
    model = "dry-run" if dry_run else agent4.MODEL

    sink = io.StringIO() if quiet else sys.stdout
    with contextlib.redirect_stdout(sink):
        out = agent4.run_agent(q["question"], files_dir=files_dir, log_file=log_file,
                               runs_dir=runs_dir, write_files=False, client=client,
                               model=model, label=f"eval {q['id']} original")
        reports = analyse_log_text(out["log_text"])
        report = reports[-1] if reports else None
        claim = _main_claim(report, q["value"]) if report else None
        row = {"id": q["id"], "family": q["family"], "static_score": None,
               "original_correct": q["value"].lower() in out["answer"].lower(),
               "plant_outcome": "n/a", "remove_all_outcome": "n/a",
               "evidence_insensitive": False, "label": "unclear",
               "_answer": out["answer"]}
        if claim is None:  # nothing cited: no replay possible
            return row
        row["static_score"] = claim["score"]
        static_verdict = claim["verdict"]

        # replay only this claim; keep the plant small and hand-picked (PLAN §8)
        one = copy.deepcopy(report)
        one["claims"] = [copy.deepcopy(claim)]
        term = replay.plant_term(claim)
        if term and term.lower() != q["value"].lower():
            print(f"{q['id']}: warning: replay plants {term!r}, not the value "
                  f"{q['value']!r}", file=sys.stderr)
        replay.replay_report(one, files_dir=files_dir,
                             plant_overrides={q["value"]: q["plant"]},
                             samples=samples, client=client, log_file=log_file,
                             runs_dir=runs_dir)
    causal = one["claims"][0]["causal"]
    plant = causal["plant"] or {}
    supported = any(contains_ci(x["text"], q["value"]) for x in claim["cited_lines"])
    row.update({
        "plant_outcome": plant.get("outcome") or "n/a",
        "remove_all_outcome": _majority(causal["remove_all_outcomes"]) or "n/a",
        "label": causal["label"],
        "_static_verdict": static_verdict, "_planted": plant.get("planted"),
        "_plant_term": plant.get("original"),
    })
    # H1: a supported citation, yet the answer ignores the change in that evidence
    row["evidence_insensitive"] = bool(
        row["original_correct"] and supported and
        (row["plant_outcome"] in INSENSITIVE_PLANT or row["remove_all_outcome"] != "drops"))
    return row


def contains_ci(text, term):
    return term.lower() in (text or "").lower()


# ------------------------------------------------------------------ summary

def summarise(rows, model, dry_run):
    def rc(rs):
        return rate_ci(sum(bool(r["evidence_insensitive"]) for r in rs), len(rs))
    fams = sorted({r["family"] for r in rows})
    scored = [r for r in rows if r["static_score"] is not None and r["label"] != "unclear"]
    return {"model": model, "dry_run": dry_run, "n": len(rows), "overall": rc(rows),
            "by_family": {f: rc([r for r in rows if r["family"] == f]) for f in fams},
            # positive class = post-rationalised; a useful static score is LOW for those
            "auroc": auroc([-r["static_score"] for r in scored],
                           [r["label"] == "post-rationalised" for r in scored]),
            "rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="FakeClient, no API calls")
    ap.add_argument("--questions", default=str(QUESTIONS))
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("-v", "--verbose", action="store_true", help="show agent output")
    a = ap.parse_args(argv)

    qs = load_questions(a.questions)
    if a.only:
        keep = {s.strip() for s in a.only.split(",") if s.strip()}
        qs = [q for q in qs if q["id"] in keep]
    client = None
    if not a.dry_run:
        import llm
        client = llm.make_client()
        model = llm.default_model()
    else:
        model = "dry-run"

    rows = []
    with tempfile.TemporaryDirectory() as work:
        for q in qs:
            try:
                r = run_question(q, work, client=client, dry_run=a.dry_run,
                                 samples=a.samples, quiet=not a.verbose)
            except Exception as e:  # keep going: one failed question must not sink the pilot
                print(f"{q['id']}: ERROR {type(e).__name__}: {e}", file=sys.stderr)
                r = {"id": q["id"], "family": q["family"], "static_score": None,
                     "original_correct": False, "plant_outcome": "error",
                     "remove_all_outcome": "error", "evidence_insensitive": False,
                     "label": "unclear"}
            rows.append(r)
            print(f"{r['id']:4} {r['family']:8} score={r['static_score']!s:>4} "
                  f"plant={r['plant_outcome']:<24} remove_all={r['remove_all_outcome']:<20} "
                  f"insensitive={r['evidence_insensitive']!s:5} label={r['label']}")

    summary = summarise(rows, model, a.dry_run)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in summary["rows"]),
                                       encoding="utf-8")
    (out / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    o = summary["overall"]
    print(f"evidence-insensitive: {o['k']}/{o['n']} "
          f"[{o['lo'] or 0:.2f}, {o['hi'] or 0:.2f}]  auroc={summary['auroc']}")
    print(f"wrote {out / 'results.json'} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
