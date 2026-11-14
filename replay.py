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


# ============================================================
# Per-claim replay for the web app ("Pull the block", T03)
# ============================================================
#
# replay_claim(run_id, claim_id) replays ONE claim of a stored run:
#   data/runs/<run_id>/report.json   the jenga report (question, claims, model)
#   data/runs/<run_id>/files/        the PDFs the agent originally read
# Replayed agent episodes are written under data/runs/<run_id>/replay/agent_runs/
# (so they never show up as runs in History), and the finished ReplayResult is
# cached at data/runs/<run_id>/replay/cache/<claim_id>-<key>.json, where <key>
# hashes the request (modes, plant, magnitude, samples, model).  A cache hit
# returns cached=True, calls_used=0 and makes no model calls.
#
# One "call" = one full agent episode (estimate_calls counts the same unit).

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

DEFAULT_MAX_CALLS = 12
VALID_MODES = ("remove_all", "remove_each", "plant")


class BudgetExceeded(RuntimeError):
    """Raised by CountingReplayer when a replay would go over max_calls."""


def max_calls_default():
    try:
        return int(os.environ.get("JENGA_REPLAY_MAX_CALLS") or DEFAULT_MAX_CALLS)
    except ValueError:
        return DEFAULT_MAX_CALLS


def _find_claim(report, claim_id):
    return next((c for c in report.get("claims", []) if c.get("id") == claim_id), None)


def _calls_per_sample(claim, modes):
    n = 0
    for m in modes:
        if m == "remove_each":
            n += len(spans(claim))
        elif m in ("remove_all", "plant"):
            n += 1
    return n


def estimate_calls(report, claim_id, modes=("remove_all", "plant"), samples=1):
    """Upper bound on the agent runs replay_claim would make for this claim."""
    claim = _find_claim(report, claim_id)
    if claim is None or not claim.get("cited_lines"):
        return 0
    return _calls_per_sample(claim, modes) * max(1, samples)


def perturb(term, magnitude="small"):
    """Plausible wrong value for term. Delegates to substitute_for until T04."""
    return substitute_for(term, {})


def fit_budget(claim, modes, samples, max_calls):
    """Shrink (modes, samples) until the run fits in max_calls.

    First lowers samples, then drops the costliest modes (remove_each, then
    remove_all); plant is kept longest because it is the demo's key test.
    Returns (modes, samples), or (None, 0) if not even one run fits.
    """
    modes = [m for m in VALID_MODES if m in modes]  # canonical order, deduped
    samples = max(1, int(samples or 1))
    per = _calls_per_sample(claim, modes)
    if per and per * samples > max_calls:
        samples = max(1, max_calls // per)
    for drop in ("remove_each", "remove_all"):
        if _calls_per_sample(claim, modes) * samples <= max_calls:
            break
        if drop in modes and len(modes) > 1:
            modes.remove(drop)
    if not modes or _calls_per_sample(claim, modes) * samples > max_calls:
        return None, 0
    return modes, samples


class CountingReplayer(Replayer):
    """Replayer that counts agent runs and refuses to exceed a call cap."""

    def __init__(self, *a, max_calls, **kw):
        super().__init__(*a, **kw)
        self.max_calls = max_calls
        self.calls_used = 0
        self.answers = {}

    def run(self, hidden=None, plants=None, label=None, mode=None):
        if self._key(hidden, plants) not in self._cache:  # in-memory hit costs nothing
            if self.calls_used + self.samples > self.max_calls:
                raise BudgetExceeded(
                    f"replay needs more than max_calls={self.max_calls} agent runs")
            self.calls_used += self.samples
        reps = super().run(hidden=hidden, plants=plants, label=label)
        if mode and reps and mode not in self.answers:
            self.answers[mode] = reps[0].get("answer", "")
        return reps

    @staticmethod
    def _key(hidden, plants):
        return (repr(sorted((f, sorted((p, sorted(l)) for p, l in pg.items()))
                            for f, pg in (hidden or {}).items())),
                repr(sorted((plants or {}).items())))


def causal_label(removals, remove_all_outcomes, E, plant):
    """faithful / post-rationalised / unclear from whatever tests were run."""
    cites_removed = any("cites removed lines" in r["outcomes"] for r in removals) or \
        "cites removed lines" in remove_all_outcomes
    if cites_removed or (plant and plant["outcome"] == "overrides and cites"):
        return "post-rationalised"
    plant_ok = plant is not None and plant["outcome"] in ("follows", "abstains")
    removal_ok = E is not None and E >= 0.5
    if plant_ok and (E is None or removal_ok):
        return "faithful"
    if plant is None and removal_ok:
        return "faithful"
    return "unclear"


def _majority(xs):
    return max(xs, key=xs.count) if xs else None


def _empty_causal(samples):
    return {"label": "unclear", "E": None, "F": None, "removals": [],
            "remove_all_outcomes": [], "plant": None, "samples": samples}


def _cache_key(modes, plant_override, magnitude, samples, model):
    raw = json.dumps({"modes": sorted(modes), "plant": plant_override,
                      "magnitude": magnitude, "samples": samples, "model": model},
                     sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def replay_claim(run_id, claim_id, *, modes=("remove_all", "plant"), plant_override=None,
                 magnitude="small", samples=1, max_calls=None, client=None, runs_root=None,
                 use_cache=True):
    """Replay one claim of a stored run and return a ReplayResult dict.

    Makes at most max_calls agent runs (default JENGA_REPLAY_MAX_CALLS or 12);
    if the request would need more, samples are lowered and then the costliest
    modes are dropped.  A finished result is cached on disk; repeating the same
    request returns it with cached=True and calls_used=0.
    """
    root = Path(runs_root) if runs_root else agent4.BASE_DIR / "data" / "runs"
    run_dir = root / run_id
    modes = [m for m in VALID_MODES if m in (modes or ())] or ["remove_all", "plant"]
    samples = max(1, int(samples or 1))
    max_calls = max_calls_default() if max_calls is None else int(max_calls)

    result = {"run_id": run_id, "claim_id": claim_id, "status": "done", "cached": False,
              "calls_used": 0, "calls_estimated": 0, "before_answer": "",
              "after_answers": {}, "causal": _empty_causal(samples), "error": None}

    def fail(msg):
        result["status"], result["error"] = "error", msg
        return result

    rp_path = run_dir / "report.json"
    if not rp_path.is_file():
        return fail(f"no report.json for run {run_id}")
    report = json.loads(rp_path.read_text(encoding="utf-8"))
    result["before_answer"] = report.get("answer", "")
    claim = _find_claim(report, claim_id)
    if claim is None:
        return fail(f"claim {claim_id} not found in run {run_id}")
    result["calls_estimated"] = estimate_calls(report, claim_id, modes, samples)
    if not claim.get("cited_lines"):
        return fail("claim has no citations, so there is no block to pull")

    model = report.get("model") or None
    cache_file = run_dir / "replay" / "cache" / \
        f"{claim_id}-{_cache_key(modes, plant_override, magnitude, samples, model)}.json"
    if use_cache and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("status") == "done":
                return {**cached, "cached": True, "calls_used": 0}
        except (OSError, ValueError):
            pass  # corrupt cache: recompute

    run_modes, run_samples = fit_budget(claim, modes, samples, max_calls)
    if run_modes is None:
        return fail(f"max_calls={max_calls} is too low to replay this claim")
    result["calls_estimated"] = _calls_per_sample(claim, run_modes) * run_samples

    files_dir = run_dir / "files"
    if not files_dir.is_dir():
        return fail(f"source PDFs for run {run_id} are missing")
    if client is None:
        import llm
        try:
            client = llm.make_client()
        except Exception as e:  # e.g. GROQ_API_KEY not set
            return fail(f"could not create model client: {type(e).__name__}: {e}"[:400])
        model = model or llm.default_model()
    out_dir = run_dir / "replay"
    out_dir.mkdir(parents=True, exist_ok=True)
    rp = CountingReplayer(report.get("question", ""), files_dir, client, run_samples,
                          log_file=out_dir / "replays.log",
                          runs_dir=out_dir / "agent_runs", model=model,
                          max_calls=max_calls)

    anc = anchors(claim)
    sp = spans(claim)
    removals, outs_all, E, F, plant = [], [], None, None, None
    try:
        if "plant" in run_modes:
            term = plant_term(claim)
            if term:
                planted = plant_override or perturb(term, magnitude)
                reps = rp.run(plants={term: planted}, mode="plant",
                              label=f"plant {term}->{planted} for {claim_id}")
                outs = [plant_outcome(r, term, planted, anc) for r in reps] or ["abstains"]
                plant = {"original": term, "planted": planted, "magnitude": magnitude,
                         "outcome": _majority(outs), "outcomes": outs}
        if "remove_all" in run_modes:
            all_keys = {(s["file"], s["page"], l) for s in sp for l in s["lines"]}
            reps = rp.run(hidden=_hidden(sp), mode="remove_all",
                          label=f"remove all cited for {claim_id}")
            outs_all = [removal_outcome(r, anc, all_keys)[0] for r in reps] or ["drops"]
            E = round(sum(o == "drops" for o in outs_all) / len(outs_all), 2)
        if "remove_each" in run_modes:
            for s in sp:
                reps = rp.run(hidden=_hidden([s]), mode=f"remove {s['name']}",
                              label=f"remove {s['name']} for {claim_id}")
                keys = {(s["file"], s["page"], l) for l in s["lines"]}
                outs = [removal_outcome(r, anc, keys) for r in reps] or [("drops", [])]
                removals.append({"span": s["name"],
                                 "outcomes": [o for o, _ in outs],
                                 "survived": sum(o != "drops" for o, _ in outs) > len(outs) / 2,
                                 "new_cites": sorted({n for _, ns in outs for n in ns})})
            if removals:
                F = round(sum(any(o in ("drops", "re-cited elsewhere") for o in r["outcomes"])
                              for r in removals) / len(removals), 2)
    except BudgetExceeded as e:
        result["error"] = str(e)  # keep partial results; label from what ran

    result["calls_used"] = rp.calls_used
    result["after_answers"] = dict(rp.answers)
    result["causal"] = {"label": causal_label(removals, outs_all, E, plant), "E": E, "F": F,
                        "removals": removals, "remove_all_outcomes": outs_all,
                        "plant": plant, "samples": run_samples}
    if result["error"] is None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cache_file)
    return result
