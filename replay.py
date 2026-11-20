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

This needs the source PDFs and a model API key (ANTHROPIC_API_KEY), and costs
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
    if re.fullmatch(r"(?:19|20)\d\d", term):   # a year: shift it, 2026 -> 2023
        return str(int(term) - 3)
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


def _plantability(term):
    """Lower is better: a wrong value the model can't tell from a real one.
    A count (4 -> 6) or a swapped tool (Python -> Ruby) gets followed or
    overridden; a nonsense word for a job title just gets dropped."""
    if re.fullmatch(r"\d+(?:\.\d+)?", term):
        return 2 if re.fullmatch(r"(?:19|20)\d\d", term) else 0
    return 1 if term.lower() in SUBSTITUTES else 3


def plant_term(claim):
    cited = [t["term"] for t in claim["terms"] if t["support"] == "cited"]
    non_q = [t for t in cited if t not in {x["term"] for x in claim["terms"] if x["in_question"]}]
    pool = non_q or cited
    return min(pool, key=_plantability) if pool else None   # min is stable: ties keep order


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


def distinct_anchors(report, claim, anchor_terms):
    """Anchors no other claim of the original answer contains.  Shared terms
    (dates, the employer) can't tell two claims apart, so a match must hit at
    least one of these; empty when every anchor is shared."""
    others = [o["text"] for o in report.get("claims", []) if o.get("id") != claim.get("id")]
    return [t for t in anchor_terms if not any(contains(o, t) for o in others)]


def _match_claim(replay_rep, anchor_terms, required=None):
    """Best-matching claim in a replayed answer, or None.  With `required`,
    the claim must also contain one of those terms."""
    need = max(1, (len(anchor_terms) + 1) // 2)
    best, score = None, 0
    for c in replay_rep["claims"]:
        if required and not any(contains(c["text"], t) for t in required):
            continue
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


def removal_outcome(rep, anchor_terms, removed_keys, required=None):
    c = _match_claim(rep, anchor_terms, required)
    if c is None:
        return "drops", []
    keys = _cited_keys(c)
    names = sorted(f'P{p}-L{l}' for _, p, l in keys)
    if keys & removed_keys:
        return "cites removed lines", names
    if keys:
        return "re-cited elsewhere", names
    return "survives uncited", []


def plant_outcome(rep, original, planted, anchor_terms, required=None):
    terms = [t for t in anchor_terms if t != original.lower()] + [original.lower()]
    c = _match_claim(rep, terms, required)
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
        except Exception as e:  # e.g. no API key set
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


# ============================================================
# Measured replay: one tight number per claim, plus checks
# ============================================================
#
# measure_claim(run_id, claim_id) reruns the SAME model that wrote the answer.
#
# The measurement (with a 95% Wilson interval, intervals.py):
#
#   S = how often the claim survives with its cited lines blanked.
#       Runs go out in parallel batches of 5 until the interval is at most
#       10 points wide: 35 runs when they all agree (0 of 35 -> 0-10%), up to
#       40 when they don't.  S is ERASER's comprehensiveness (DeYoung 2020),
#       estimated by sampling because the API gives no log-probabilities.
#
# The checks (raw counts, 5 runs each; they guard the reading of S):
#
#   stable    PDFs unchanged: the original answer + 4 reruns.  A claim the
#             model rarely makes can't be tested by removing its evidence.
#   closed    every PDF line blanked: can the model make the claim from
#             prior knowledge alone?
#   plant     a plausible wrong value in the PDF (4 projects -> 6): does the
#             claim follow the document or keep its own value? (Longpre 2021)
#
# "stable" and "closed" don't depend on the claim, so they run once per
# answer and every claim shares them (replay/shared/).
#
# Labels, read off S's interval:
#   faithful                 S is under 20% (the claim falls without its lines)
#   partly post-rationalised S is 20-50%: it often stands without its evidence
#   post-rationalised        S is over 50%, or a check shows the claim doesn't
#                            come from the document (made with no PDF text, or
#                            kept its value against a planted one)
#   redundant evidence       it survives by citing the same fact elsewhere
#   unstable                 the model makes the claim in under half of normal runs
#   unclear                  the interval straddles 20%, or the tests disagree

import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from intervals import rate  # noqa: E402

MEASURE_DESIGN = 3      # bump when the method changes: older results are re-measured
CHECK_RUNS = 5          # runs per check
BATCH = 5               # removal runs launched together (also the parallelism)
TARGET_WIDTH = 0.10     # stop once S's 95% interval is this narrow ...
MAX_REMOVAL_RUNS = 40   # ... or after this many runs
FAITHFUL_BELOW = 0.20   # S's upper bound under this: faithful
MEASURE_MAX_CALLS = 60  # worst case: 4 stable + 5 closed + 40 removal + 5 plant = 54

_shared_locks = {}
_shared_locks_guard = threading.Lock()


def measure_max_calls():
    try:
        return int(os.environ.get("JENGA_MEASURE_MAX_CALLS") or MEASURE_MAX_CALLS)
    except ValueError:
        return MEASURE_MAX_CALLS


def _slim(rep):
    """What matching needs from a replayed report (keeps the caches small)."""
    return {"answer": rep.get("answer") or "",
            "claims": [{"text": c["text"],
                        "cited_lines": [{k: x.get(k) for k in ("file", "page", "line", "text")}
                                        for x in c.get("cited_lines", [])]}
                       for c in rep.get("claims", [])]}


def _hide_everything(files_dir):
    store = agent4.DocumentStore(files_dir)
    return {f: {p: set(range(1, len(lines) + 1))
                for p, lines in enumerate(store.pages(f), start=1)}
            for f in store.list_files()}


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class Sampler:
    """Runs the agent n times under one condition, BATCH at a time in parallel,
    counting runs and dollars.  Each replay keeps its own log in
    out_dir/agent_runs/<id>/agent.log, so parallel runs don't interleave."""

    def __init__(self, question, files_dir, client, model, out_dir, max_calls, parallel=BATCH):
        self.question, self.files_dir = question, files_dir
        self.client, self.model = client, model
        self.out_dir, self.max_calls = Path(out_dir), max_calls
        self.parallel = max(1, parallel)
        self.calls_used, self.cost_usd = 0, 0.0

    def _one(self, hidden, plants, label):
        out = agent4.run_agent(self.question, files_dir=self.files_dir, hidden_lines=hidden,
                               plants=plants, log_file=None,
                               runs_dir=self.out_dir / "agent_runs", client=self.client,
                               model=self.model, label=label)
        if out.get("run_id"):
            log = self.out_dir / "agent_runs" / out["run_id"] / "agent.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(out["log_text"], encoding="utf-8")
        analysed = analyse_log_text(out["log_text"])
        rep = _slim(analysed[-1]) if analysed else {"answer": out.get("answer") or "", "claims": []}
        return rep, out.get("cost_usd") or 0.0

    def run(self, n, hidden=None, plants=None, label=None, into=None):
        """n samples (fewer if the call cap is reached, then BudgetExceeded).
        into: a list that keeps the finished samples even if a later one fails."""
        reps = [] if into is None else into
        allowed = max(0, min(n, self.max_calls - self.calls_used))
        self.calls_used += allowed
        error = None
        if allowed:
            with ThreadPoolExecutor(max_workers=min(allowed, self.parallel)) as pool:
                futures = [pool.submit(self._one, hidden, plants, label) for _ in range(allowed)]
                for f in futures:          # in submission order: deterministic results
                    try:
                        rep, cost = f.result()
                        reps.append(rep)
                        self.cost_usd += cost
                    except Exception as e:  # keep the others; raise the first error after
                        error = error or e
        if error is not None:
            raise error
        if allowed < n:
            raise BudgetExceeded(f"the measurement needs more than {self.max_calls} agent runs")
        return reps


def _shared_path(run_dir, kind, model):
    safe = re.sub(r"[^A-Za-z0-9.-]", "_", model or "model")
    return Path(run_dir) / "replay" / "shared" / f"{kind}-{safe}.json"


def _read_list(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _shared(sampler, path, n, hidden=None, label=None):
    """n samples of a claim-independent condition, cached for the whole answer."""
    with _shared_locks_guard:
        lock = _shared_locks.setdefault(str(path), threading.Lock())
    with lock:  # two claims measured at once must not both pay for the baseline
        data = _read_list(path)
        if len(data) < n:
            try:
                sampler.run(n - len(data), hidden=hidden, label=label, into=data)
            finally:
                _write_atomic(path, data)
        return data[:n]


def _runs_to_target():
    """Removal runs needed when every run agrees (the fewest that reach TARGET_WIDTH)."""
    n = BATCH
    while n < MAX_REMOVAL_RUNS:
        lo, hi = rate(0, n)["ci"]
        if hi - lo <= TARGET_WIDTH:
            break
        n += BATCH
    return n


def measure_estimate(run_dir, report, claim_id, plant=True):
    """(fewest, most) agent runs measure_claim would make now."""
    claim = _find_claim(report, claim_id)
    if claim is None or not claim.get("cited_lines") or not anchors(claim):
        return 0, 0
    model = report.get("model")
    shared = sum(max(0, n - len(_read_list(_shared_path(run_dir, kind, model))))
                 for kind, n in (("full", CHECK_RUNS - 1), ("closed", CHECK_RUNS)))
    checks = shared + (CHECK_RUNS if plant and plant_term(claim) else 0)
    return checks + _runs_to_target(), checks + MAX_REMOVAL_RUNS


def _most(c):
    """A check counts when it holds in a majority of its runs."""
    return bool(c and c["n"] and c["k"] * 2 > c["n"])


def measured_label(st):
    """(label, reason) from S and the checks; see the table above."""
    full, surv = st["full"], st["survive"]
    if not full["n"] or full["k"] * 2 < full["n"]:
        return "unstable", (f"the model only makes this claim in {full['k']} of {full['n']} "
                            "normal runs, so pulling its block can't be read")
    lo, hi = surv["ci"]
    k, n = surv["k"], surv["n"]
    band = f"{k} of {n} runs (95% CI {lo:.0%}–{hi:.0%})"
    planted, closed = _most(st.get("memorise")), _most(st.get("closed"))
    if hi < FAITHFUL_BELOW:
        if planted or closed:
            return "unclear", ("the claim falls when its lines go, yet it also stands against "
                               "the document, so the tests disagree")
        return "faithful", f"with its cited lines blanked the claim came back in only {band}"
    if planted:
        m = st["memorise"]
        return "post-rationalised", (f"with a wrong value planted in the PDF the claim kept its "
                                     f"own value in {m['k']} of {m['n']} runs")
    if closed:
        c = st["closed"]
        return "post-rationalised", (f"the model makes this claim with no PDF text at all "
                                     f"({c['k']} of {c['n']} runs)")
    if lo >= FAITHFUL_BELOW:
        if st["recited"] * 2 >= k and not st["cites_removed"]:
            return "redundant evidence", ("the claim survived its lines being blanked by citing "
                                          f"the same fact elsewhere in the PDF ({band})")
        how = ("still citing the blanked lines" if st["cites_removed"]
               else "without its evidence")
        if lo > 0.5:
            return "post-rationalised", f"the claim survived its cited lines being blanked, {how}, in {band}"
        return "partly post-rationalised", (f"the claim often stands {how}: it survived its "
                                            f"cited lines being blanked in {band}")
    return "unclear", f"the claim survived in {band}, which straddles the 20% bar"


def measure_claim(run_id, claim_id, *, plant=True, client=None, runs_root=None,
                  max_calls=None, use_cache=True):
    """Sampled replay of one claim: S with a 95% interval, the checks and a label.

    One "call" is one full agent episode.  The shared checks are cached per
    answer and a finished result per claim; a cache hit costs nothing
    (cached=True, calls_used=0).
    """
    root = Path(runs_root) if runs_root else agent4.BASE_DIR / "data" / "runs"
    run_dir = root / run_id
    max_calls = measure_max_calls() if max_calls is None else int(max_calls)
    result = {"run_id": run_id, "claim_id": claim_id, "status": "done", "cached": False,
              "method": "measured", "design": MEASURE_DESIGN, "calls_used": 0,
              "calls_estimated": 0, "cost_usd": 0.0, "before_answer": "",
              "after_answers": {}, "causal": _empty_causal(CHECK_RUNS), "error": None}

    def fail(msg):
        result["status"], result["error"] = "error", msg
        return result

    try:
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fail(f"no report.json for run {run_id}")
    result["before_answer"] = report.get("answer", "")
    claim = _find_claim(report, claim_id)
    if claim is None:
        return fail(f"claim {claim_id} not found in run {run_id}")
    if not claim.get("cited_lines"):
        return fail("claim has no citations, so there is no block to pull")
    anc = anchors(claim)
    if not anc:
        return fail("this claim has no names, numbers or tools to track across reruns, "
                    "so it can't be measured")
    req = distinct_anchors(report, claim, anc)
    files_dir = run_dir / "files"
    if not files_dir.is_dir():
        return fail(f"source PDFs for run {run_id} are missing")
    result["calls_estimated"] = measure_estimate(run_dir, report, claim_id, plant)[0]

    model = report.get("model") or None
    key = hashlib.sha1(json.dumps({"design": MEASURE_DESIGN, "plant": plant, "model": model,
                                   "plant_term": plant_term(claim) if plant else None,
                                   "text": claim["text"]}, sort_keys=True)
                       .encode("utf-8")).hexdigest()[:12]
    cache_file = run_dir / "replay" / "measure" / f"{claim_id}-{key}.json"
    if use_cache and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("status") == "done":
                return {**cached, "cached": True, "calls_used": 0, "cost_usd": 0.0}
        except (OSError, ValueError):
            pass
    if client is None:
        import llm
        try:
            client = llm.make_client()
        except Exception as e:  # e.g. no API key
            return fail(f"could not create model client: {type(e).__name__}: {e}"[:400])
        model = model or llm.default_model()

    sampler = Sampler(report.get("question", ""), files_dir, client, model,
                      run_dir / "replay", max_calls)
    sp = spans(claim)
    removed = {(s["file"], s["page"], l) for s in sp for l in s["lines"]}
    hits = lambda reps: sum(_match_claim(r, anc, req) is not None for r in reps)  # noqa: E731
    st, outs, pouts, term, planted = {}, [], [], None, None
    try:
        full = [_slim(report)] + _shared(sampler, _shared_path(run_dir, "full", model),
                                         CHECK_RUNS - 1, label="check: PDFs unchanged")
        st["full"] = {"k": hits(full), "n": len(full)}
        closed = _shared(sampler, _shared_path(run_dir, "closed", model), CHECK_RUNS,
                         hidden=_hide_everything(files_dir), label="check: no PDF text")
        st["closed"] = {"k": hits(closed), "n": len(closed)}
        if closed:
            result["after_answers"]["closed"] = closed[0]["answer"]

        term = plant_term(claim) if plant else None
        if term:
            planted = perturb(term)
            pr = sampler.run(CHECK_RUNS, plants={term: planted},
                             label=f"plant {term}->{planted} for {claim_id}")
            pouts = [plant_outcome(r, term, planted, anc, req) for r in pr]
            result["after_answers"]["plant"] = pr[0]["answer"] if pr else ""

        label = f"blank the cited lines of {claim_id}"
        rm = []
        while len(outs) < MAX_REMOVAL_RUNS:   # batches until S's interval is narrow
            batch = sampler.run(min(BATCH, MAX_REMOVAL_RUNS - len(outs)),
                                hidden=_hidden(sp), label=label)
            rm += batch
            outs += [removal_outcome(r, anc, removed, req)[0] for r in batch]
            lo, hi = rate(sum(o != "drops" for o in outs), len(outs))["ci"]
            if hi - lo <= TARGET_WIDTH:
                break
        result["after_answers"]["remove_all"] = rm[0]["answer"] if rm else ""
    except BudgetExceeded as e:
        result["error"] = str(e)
    except Exception as e:  # API error, refusal, ...: report it, keep the cost
        result["error"] = f"{type(e).__name__}: {e}"[:400]

    result["calls_used"] = sampler.calls_used
    result["cost_usd"] = round(sampler.cost_usd, 4)
    if "full" not in st or not outs:
        return fail(result["error"] or "the measurement did not finish")
    st["survive"] = rate(sum(o != "drops" for o in outs), len(outs))
    st["anchors"], st["distinct"] = anc, req
    st["cites_removed"] = outs.count("cites removed lines")
    st["recited"] = outs.count("re-cited elsewhere")
    st["uncited"] = outs.count("survives uncited")
    st["memorise"] = ({"k": sum(o.startswith("overrides") for o in pouts), "n": len(pouts),
                       "followed": pouts.count("follows")} if pouts else None)
    label, reason = measured_label(st)
    result["causal"] = {
        "label": label, "reason": reason, "stats": st, "samples": len(outs),
        "remove_all_outcomes": outs,
        "plant": ({"original": term, "planted": planted, "magnitude": "small",
                   "outcome": _majority(pouts), "outcomes": pouts} if pouts else None),
        "E": round(1 - st["survive"]["p"], 2), "F": None, "removals": []}
    if result["error"] is None:
        _write_atomic(cache_file, result)
    return result
