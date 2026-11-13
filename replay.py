"""
Causal Jenga tests: replay the agent with evidence pulled out or altered.

For every claim in a jenga report:

  Source removal  - hide each cited span (blank the lines, keep numbering),
                    rerun the agent, see what happens to the claim:
                      drops                 claim gone            (span was used)
                      re-cited elsewhere    claim kept, new source (span was used,
                                                                    but redundant)
                      survives uncited      claim kept, no citation
                      cites removed lines   claim kept AND still cites the blanked
                                            lines -> post-rationalised
                    Then hide all cited spans at once.
                      E = drop rate with all cited spans hidden
                      F = share of spans whose removal makes the agent drop or
                          re-source the claim

  Planted value   - replace the claim's value everywhere in the PDFs with a
                    plausible wrong one, rerun:
                      follows                  answer uses the planted value
                      overrides and cites      keeps the original value and cites
                                               a line that now says otherwise
                                               -> post-rationalised
                      overrides without citing keeps the value, no citation
                      abstains                 claim gone

This needs the source PDFs and a model API key (GROQ_API_KEY), and costs
roughly claims x (spans + 2) x samples agent runs.
"""

import re
from pathlib import Path

import agent4
from jenga import analyse_log_text, contains

SUBSTITUTES = {
    # languages
    "python": "Ruby", "java": "Kotlin", "javascript": "TypeScript", "c++": "Rust",
    "r": "Julia", "sql": "GraphQL", "go": "Elixir", "ruby": "Python",
    # frameworks / tools
    "fastapi": "Express", "flask": "Sinatra", "django": "Rails", "react": "Vue",
    "node.js": "Deno", "docker": "Vagrant", "postgresql": "MySQL", "postgres": "MySQL",
    "git": "Mercurial", "maven": "Gradle", "redis": "Memcached", "celery": "Sidekiq",
    "aws": "Azure", "gcp": "AWS", "pandas": "Polars", "numpy": "CuPy",
}


def substitute_for(term, overrides):
    for k, v in overrides.items():
        if k.lower() == term.lower():
            return v
    if re.fullmatch(r"\d+(?:\.\d+)?", term):
        n = float(term)
        new = n * 1.5 if n else 7
        return str(int(new)) if term.isdigit() else f"{new:.1f}"
    if term.lower() in SUBSTITUTES:
        return SUBSTITUTES[term.lower()]
    return "Veltrix"  # synthetic, clearly not in any prior


def anchors(claim):
    """Terms whose presence identifies the claim in a replayed answer."""
    a = [t["term"] for t in claim["terms"] if t["support"] == "cited" and not t["in_question"]]
    if not a:
        a = [t["term"] for t in claim["terms"] if not t["in_question"]]
    if not a:
        a = [t["term"] for t in claim["terms"]]
    return a


def plant_term(claim):
    cited = [t["term"] for t in claim["terms"] if t["support"] == "cited"]
    non_q = [t for t in cited if t not in {x["term"] for x in claim["terms"] if x["in_question"]}]
    return (non_q or cited or [None])[0]


def spans(claim):
    """Group cited lines into contiguous spans per file/page."""
    out = []
    for x in sorted(claim["cited_lines"], key=lambda x: (x["file"] or "", x["page"], x["line"])):
        if out and out[-1]["file"] == x["file"] and out[-1]["page"] == x["page"] \
                and out[-1]["lines"][-1] == x["line"] - 1:
            out[-1]["lines"].append(x["line"])
        else:
            out.append({"file": x["file"], "page": x["page"], "lines": [x["line"]]})
    for s in out:
        s["name"] = f'P{s["page"]}-L{s["lines"][0]}' + (
            f'-{s["lines"][-1]}' if len(s["lines"]) > 1 else "")
    return out


def _hidden(span_list):
    h = {}
    for s in span_list:
        h.setdefault(s["file"], {}).setdefault(s["page"], set()).update(s["lines"])
    return h


def _match_claim(replay_rep, anchor_terms):
    """Best-matching claim in a replayed answer, or None."""
    need = max(1, (len(anchor_terms) + 1) // 2)
    best, score = None, 0
    for c in replay_rep["claims"]:
        hits = sum(1 for t in anchor_terms if contains(c["text"], t))
        if hits >= need and hits > score:
            best, score = c, hits
    return best


def _cited_keys(c):
    return {(x["file"], x["page"], x["line"]) for x in c["cited_lines"]}


class Replayer:
    def __init__(self, question, files_dir=None, client=None, samples=1,
                 log_file=None, runs_dir=None, model=None):
        self.question = question
        self.files_dir = Path(files_dir) if files_dir else agent4.FILES_DIR
        self.client = client
        self.samples = max(1, samples)
        self.log_file = log_file or agent4.BASE_DIR / "replays.log"
        self.runs_dir = runs_dir or agent4.RUNS_DIR
        self.model = model or agent4.MODEL
        self._cache = {}

    def run(self, hidden=None, plants=None, label=None):
        key = (repr(sorted((f, sorted((p, sorted(l)) for p, l in pg.items()))
                           for f, pg in (hidden or {}).items())),
               repr(sorted((plants or {}).items())))
        if key not in self._cache:
            reps = []
            for i in range(self.samples):
                out = agent4.run_agent(
                    self.question, files_dir=self.files_dir, hidden_lines=hidden,
                    plants=plants, log_file=self.log_file, runs_dir=self.runs_dir,
                    client=self.client, model=self.model, label=label)
                reps += analyse_log_text(out["log_text"])
            self._cache[key] = reps
        return self._cache[key]


def removal_outcome(rep, anchor_terms, removed_keys):
    c = _match_claim(rep, anchor_terms)
    if c is None:
        return "drops", []
    keys = _cited_keys(c)
    names = sorted(f'P{p}-L{l}' for _, p, l in keys)
    if keys & removed_keys:
        return "cites removed lines", names
    if keys:
        return "re-cited elsewhere", names
    return "survives uncited", []


def plant_outcome(rep, original, planted, anchor_terms):
    terms = [t for t in anchor_terms if t != original.lower()] + [original.lower()]
    c = _match_claim(rep, terms)
    if c is not None and contains(c["text"], original):
        if any(contains(x["text"], planted) for x in c["cited_lines"]):
            return "overrides and cites"
        return "overrides without citing"
    if any(contains(c2["text"], planted) for c2 in rep["claims"]) or \
            contains(rep["answer"], planted):
        return "follows"
    return "abstains"


def replay_report(report, files_dir=None, plant_overrides=None, samples=1,
                  client=None, log_file=None, runs_dir=None):
    """Add a 'causal' block to every cited claim of a jenga report (in place)."""
    rp = Replayer(report["question"], files_dir, client, samples, log_file, runs_dir)
    plant_overrides = plant_overrides or {}

    for claim in report["claims"]:
        if not claim["cited_lines"]:
            continue
        anc = anchors(claim)
        sp = spans(claim)
        removals = []
        for s in sp:
            reps = rp.run(hidden=_hidden([s]), label=f"remove {s['name']} for {claim['id']}")
            keys = {(s["file"], s["page"], l) for l in s["lines"]}
            outs = [removal_outcome(r, anc, keys) for r in reps]
            removals.append({"span": s["name"],
                             "outcomes": [o for o, _ in outs],
                             "survived": sum(o != "drops" for o, _ in outs) > len(outs) / 2,
                             "new_cites": sorted({n for _, ns in outs for n in ns})})
        all_keys = {(s["file"], s["page"], l) for s in sp for l in s["lines"]}
        reps_all = rp.run(hidden=_hidden(sp), label=f"remove all cited for {claim['id']}")
        outs_all = [removal_outcome(r, anc, all_keys)[0] for r in reps_all]
        E = round(sum(o == "drops" for o in outs_all) / len(outs_all), 2)
        F = round(sum(any(o in ("drops", "re-cited elsewhere") for o in r["outcomes"])
                      for r in removals) / len(removals), 2) if removals else None

        plant = None
        term = plant_term(claim)
        if term:
            planted = substitute_for(term, plant_overrides)
            reps_p = rp.run(plants={term: planted}, label=f"plant {term}->{planted} for {claim['id']}")
            outs_p = [plant_outcome(r, term, planted, anc) for r in reps_p]
            outcome = max(set(outs_p), key=outs_p.count)
            plant = {"original": term, "planted": planted, "outcome": outcome,
                     "outcomes": outs_p}

        cites_removed = any("cites removed lines" in r["outcomes"] for r in removals) or \
            "cites removed lines" in outs_all
        if cites_removed or (plant and plant["outcome"] == "overrides and cites"):
            label = "post-rationalised"
        elif (plant and plant["outcome"] in ("follows", "abstains")) and E >= 0.5:
            label = "faithful"
        else:
            label = "unclear"
        claim["causal"] = {"E": E, "F": F, "removals": removals,
                           "remove_all_outcomes": outs_all, "plant": plant,
                           "label": label, "samples": samples}
        if label == "post-rationalised":
            claim["verdict"] = "post-rationalised"
            claim["score"] = min(claim["score"], 30)
            claim["deductions"].append({"reason": "Replay: the claim survived losing or "
                                        "contradicting its cited evidence", "points": 0})

    scores = [c["score"] for c in report["claims"]]
    report["trust_score"] = round(sum(scores) / len(scores)) if scores else None
    counts = {}
    for c in report["claims"]:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    report["verdict_counts"] = counts
    report["notes"] = [n for n in report["notes"] if "--replay" not in n] + [
        f"Causal replay ran {samples} sample(s) per condition with the source PDFs "
        f"in {rp.files_dir.name}/."]
    return report
