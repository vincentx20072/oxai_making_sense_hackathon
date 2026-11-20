"""
Haiku judge: does each claim actually follow from the PDF lines?

Alibi's keyword check asks "are the claim's words in the cited lines?".  The
judge asks the stronger question, the way ALCE / FActScore score attributions:

  1. split every claim into atomic facts (one checkable statement each);
  2. for each fact, find the shown PDF lines that state it, and mark it
     supported / not found / contradicted.

Everything after that is counting, done here and not by the model.  A claim's
verdict is its worst fact, one of four:

  contradicted   a fact contradicts the PDF
  not found      a fact isn't in the PDF (or the claim has no checkable fact)
  miscited       every fact is in the PDF, but not all in the lines it cites
  supported      every fact is in the lines it cites

The claim's kind (document fact / judgement / outside knowledge) is shown
beside it; it doesn't change the rule.

One call per run (all claims at once, structured JSON output), cached in
data/runs/<id>/judge.json.  Model: llm.judge_model() (claude-haiku-5-5).
"""

import hashlib
import json

import intervals
import jenga
import llm

KINDS = ("document fact", "judgement", "outside knowledge")
STATUSES = ("supported", "not found", "contradicted")

SYSTEM = """You check an AI agent's answer against the PDF lines it was shown.

You get the user's question, the numbered source lines, and the agent's claims
(each with the line numbers it cited).  For every claim:

- kind: "document fact" if it states something the documents could confirm or
  deny; "judgement" if it is an opinion, assessment, inference or recommendation;
  "outside knowledge" if it states a fact about the world that is not about the
  documents (e.g. what a company looks for in hires).
- atoms: split the claim into atomic facts about the documents, one checkable
  statement each.  For a judgement, list the document facts it rests on (its
  premises); a pure opinion with no factual premise has no atoms.  Do not
  include outside-knowledge statements as atoms.
- For each atom give status "supported" (a source line states it, paraphrase
  and obvious equivalents allowed, e.g. "2K+" = "over 2,000"), "contradicted"
  (a line states otherwise) or "not found", and the numbers of the lines that
  support or contradict it (empty when not found).
- A claim that the documents do NOT mention something is an absence atom:
  write the atom as the absence ("the resume does not mention clearance") and
  mark it "supported" with no lines if no line mentions it, "contradicted"
  with the line if one does.
- Judge only against the numbered lines, never your own knowledge.  Be strict
  about specifics: a technology, number, date, name or title must appear.
- note: one short sentence on anything wrong (empty string if nothing)."""

SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "atoms": {"type": "array", "items": {
                "type": "object",
                "properties": {"fact": {"type": "string"},
                               "status": {"type": "string", "enum": list(STATUSES)},
                               "lines": {"type": "array", "items": {"type": "integer"}}},
                "required": ["fact", "status", "lines"],
                "additionalProperties": False}},
            "note": {"type": "string"}},
        "required": ["id", "kind", "atoms", "note"],
        "additionalProperties": False}}},
    "required": ["claims"],
    "additionalProperties": False,
}

# verdict -> chip style (same bands as the rest of the UI)
VERDICTS = ("supported", "miscited", "not found", "contradicted")   # best to worst
STYLE = {"supported": "ok", "miscited": "warn", "not found": "bad", "contradicted": "bad"}


def _sig(report):
    """Changes whenever the claims or their citations change."""
    data = [(c["id"], c["text"], [(x["file"], x["page"], x["start"], x["end"])
                                  for x in c.get("citations", [])])
            for c in report["claims"]]
    return hashlib.sha1(json.dumps(data).encode()).hexdigest()[:16]


def number_lines(run):
    """[(n, (file, page, line), text)] for every PDF line shown to the agent."""
    seen = jenga._seen_map(run)
    keys = sorted(seen, key=lambda k: (k[0] or "", k[1], k[2]))
    return [(i, k, seen[k]["text"]) for i, k in enumerate(keys, start=1)]


def build_prompt(question, report, numbered):
    index = {k: n for n, k, _ in numbered}
    files = {k[0] for _, k, _ in numbered}
    one = len(files) == 1
    src = "\n".join(f"#{n} [{'' if one else (k[0] or '?') + ' '}p{k[1]} L{k[2]}] {t}"
                    for n, k, t in numbered)
    rows = []
    for c in report["claims"]:
        cited = cited_numbers(c, index)
        rows.append(f"{c['id']} (cites lines {', '.join(f'#{n}' for n in cited) or 'none'}): {c['text']}")
    return (f"QUESTION: {question}\n\nSOURCE LINES (shown to the agent):\n{src or '(none)'}"
            f"\n\nCLAIMS:\n" + "\n".join(rows))


def cited_numbers(claim, index):
    out = []
    for x in claim.get("citations", []):
        for l in range(x["start"], x["end"] + 1):
            for f in ({x["file"]} | ({k[0] for k in index} if x["file"] is None else set())):
                n = index.get((f, x["page"], l))
                if n and n not in out:
                    out.append(n)
    return sorted(out)


def score_claim(item, cited):
    """Counts and a verdict from the judge's atoms (no model involved)."""
    atoms = item.get("atoms", [])
    sup = [a for a in atoms if a["status"] == "supported"]
    contra = [a for a in atoms if a["status"] == "contradicted"]
    cited = set(cited)
    located = [a for a in sup if a["lines"]]   # absence atoms have no line to cite
    in_cited = [a for a in located if cited & set(a["lines"])]
    used = sorted(cited & {l for a in sup for l in a["lines"]})
    kind = item.get("kind") or "document fact"
    if contra:
        verdict = "contradicted"
    elif not atoms or len(sup) < len(atoms):
        verdict = "not found"        # a fact missing, or nothing checkable
    elif len(in_cited) < len(located):
        verdict = "miscited"         # all true, but (partly) from lines it didn't cite
    else:
        verdict = "supported"
    return {"kind": kind, "verdict": verdict, "style": STYLE[verdict],
            "atoms": atoms, "note": item.get("note") or "",
            "n_atoms": len(atoms), "n_supported": len(sup), "n_contradicted": len(contra),
            "n_in_cited": len(in_cited), "cited_lines": sorted(cited),
            "cited_used": used}


def judge_run(run, report, client, model=None):
    """One structured-output call; returns the judge.json payload."""
    model = model or llm.judge_model()
    numbered = number_lines(run)
    index = {k: n for n, k, _ in numbered}
    prompt = build_prompt(report.get("question") or run.get("question", ""), report, numbered)
    response = client.messages.create(
        model=model, max_tokens=16000, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"The judge stopped early ({response.stop_reason}).")
    text = next(b.text for b in response.content if b.type == "text")
    items = {i["id"]: i for i in json.loads(text)["claims"]}
    usage = llm.add_usage({}, response.usage)
    labels = {n: (k[1], k[2], t) for n, k, t in numbered}
    claims = {}
    for c in report["claims"]:
        if c["id"] in items:
            res = score_claim(items[c["id"]], cited_numbers(c, index))
            for a in res["atoms"]:   # readable refs for the UI
                a["refs"] = [f"p{labels[l][0]} L{labels[l][1]}" for l in a["lines"] if l in labels]
            claims[c["id"]] = res
    facts = [r for r in claims.values() if r["n_atoms"]]
    totals = {"atoms": sum(r["n_atoms"] for r in facts),
              "supported": sum(r["n_supported"] for r in facts),
              "contradicted": sum(r["n_contradicted"] for r in facts),
              "verdicts": _count(r["verdict"] for r in claims.values())}
    return {"model": getattr(response, "model", model), "sig": _sig(report),
            "usage": usage, "cost_usd": llm.cost_usd(model, usage), "claims": claims,
            "totals": with_interval(totals)}


def with_interval(totals):
    """Add the 95% Wilson interval for the share of atomic facts found (FActScore
    precision).  It treats each fact as one check, so it covers sampling noise
    in this answer's facts, not the judge's own mistakes."""
    totals["ci"] = intervals.wilson(totals.get("supported", 0), totals.get("atoms", 0))
    return totals


def rescore(judged):
    """Recount verdicts from the stored facts, so a judge.json written under
    older verdict names reads with the current four (no model call)."""
    for r in judged.get("claims", {}).values():
        r.update(score_claim({"kind": r.get("kind"), "atoms": r.get("atoms", []),
                              "note": r.get("note", "")}, r.get("cited_lines", [])))
    judged.setdefault("totals", {})["verdicts"] = _count(
        r["verdict"] for r in judged.get("claims", {}).values())
    return with_interval(judged["totals"])


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def is_current(judged, report):
    return bool(judged) and judged.get("sig") == _sig(report)
