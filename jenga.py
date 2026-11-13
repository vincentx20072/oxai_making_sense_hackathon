"""
Jenga: did the agent actually USE the evidence it cites?

Reads agent logs (the agent4.log format, old or instrumented), extracts every
claim and citation in the final answer, and scores each claim for
post-rationalisation: a citation that supports the claim without having
caused it.

Two tiers of signal
-------------------
Static (log only, no model calls, safe to run on any uploaded log):
  * grounding     - was every cited line actually shown to the agent?
                    (opened via read / snippet only via search / never seen)
  * E_lex         - evidence share: fraction of the claim's specific terms
                    (names, values, technologies, the question's focus) that
                    the cited lines contain.  A lexical stand-in for the
                    plan's log-prob evidence share E.
  * bridging      - the question's focus term is in the claim but NOT in the
                    cited lines: the link from evidence to answer comes from
                    the model's prior, not the source.
  * jenga pull    - leave-one-out over cited lines: which line is
                    load-bearing (supplies a term no other cited line does)
                    and which is dead weight (supplies nothing).
  * timing        - did a claim value appear in the agent's own text
                    (reasoning, messages, tool arguments) before any tool
                    result containing it?  "Searching to confirm".
  * selection     - are the cited lines the ones the agent selected?

Causal (replay.py, needs the source PDFs and a model API key):
  * source removal: rerun with each cited span hidden, then all of them.
  * planted value:  rerun with the claim's value replaced in every document.
  These produce the plan's E and F and the replay labels
  (follows / overrides-and-cites / overrides-without-citing / abstains).

Usage
-----
  python jenga.py samples/agent4.log                 # writes report + infographic
  python jenga.py run.log --out out/ --replay        # also run causal tests

Library use (e.g. from the website):
  reports = analyse_log_text(text)          # list of report dicts, one per run
  html    = render_html(reports)            # self-contained infographic
"""

import argparse
import html as _html
import json
import math
import re
import sys
from pathlib import Path

# ============================================================
# 1. LOG PARSING
# ============================================================

TS_RE = re.compile(r"^\[(\d{4}-\d\d-\d\dT[\d:.]+)\] ?(.*)$")
REF_LINE_RE = re.compile(r"^\[P(\d+)-L(\d+)\] ?(.*)$")
BLOCK_RE = re.compile(r"----- BEGIN ([A-Z ]+) -----\n(.*?)\n?----- END \1 -----", re.S)
FROM_RE = re.compile(r'FROM "([^"]+)"')


def _entries(text):
    """Split a log into (timestamp, head, full_text) entries."""
    out = []
    for raw in text.splitlines():
        m = TS_RE.match(raw)
        if m:
            out.append([m.group(1), m.group(2), [m.group(2)]])
        elif out:
            out[-1][2].append(raw)
    return [(ts, head, "\n".join(body)) for ts, head, body in out]


def _block(entry_text):
    m = BLOCK_RE.search(entry_text)
    return m.group(2) if m else ""


def _ref_lines(body):
    for line in body.splitlines():
        m = REF_LINE_RE.match(line)
        if m:
            yield int(m.group(1)), int(m.group(2)), m.group(3)


def parse_log(text):
    """Parse a log into a list of runs (one per NEW QUESTION)."""
    text = text.replace("\r\n", "\n")
    runs, run = [], None
    last_tool = None

    for ts, head, full in _entries(text):
        if head.startswith("NEW QUESTION:"):
            run = {"question": head.split(":", 1)[1].strip(), "model": None,
                   "run_id": None, "label": None, "plants": {}, "events": [],
                   "answer": None, "selected": [], "started": ts,
                   "has_agent_text": False}
            runs.append(run)
            last_tool = None
            continue
        if run is None:
            continue
        ev = run["events"]

        if head.startswith("MODEL:"):
            run["model"] = head.split(":", 1)[1].strip()
        elif head.startswith("RUN ID:"):
            run["run_id"] = head.split(":", 1)[1].strip()
        elif head.startswith("RUN LABEL:"):
            run["label"] = head.split(":", 1)[1].strip()
        elif head.startswith("REPLAY PLANTS:"):
            try:
                run["plants"] = json.loads(head.split(":", 1)[1])
            except json.JSONDecodeError:
                pass
        elif head.startswith("MODEL REQUEST #"):
            ev.append({"kind": "request", "n": int(re.sub(r"\D", "", head) or 0), "ts": ts})
        elif head.startswith(("MODEL REASONING", "MODEL TEXT BEFORE TOOL CALL")):
            run["has_agent_text"] = True
            ev.append({"kind": "agent_text", "text": _block(full), "ts": ts,
                       "source": "reasoning" if "REASONING" in head else "message"})
        elif head.startswith("MODEL REQUESTED TOOL:"):
            m = re.match(r"MODEL REQUESTED TOOL: (\w+)\((.*)\)\s*$", head, re.S)
            name, args = (m.group(1), m.group(2)) if m else (head[22:].strip(), "{}")
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
            last_tool = {"kind": "tool_request", "tool": name, "args": args, "ts": ts}
            ev.append(last_tool)
        elif (head.startswith(("TOOL RESULT PROVIDED", "PDF SEARCH RESULTS PROVIDED",
                               "PDF CONTENT PROVIDED"))):
            body = _block(full)
            fm = FROM_RE.search(head)
            filename = fm.group(1) if fm else (last_tool or {}).get("args", {}).get("filename")
            mode = ("search" if head.startswith("PDF SEARCH") else
                    "read" if head.startswith("PDF CONTENT") else "other")
            ev.append({"kind": "tool_result", "mode": mode, "file": filename,
                       "tool": (last_tool or {}).get("tool"), "text": body,
                       "lines": list(_ref_lines(body)), "ts": ts})
        elif head.startswith("MODEL SELECTED EVIDENCE"):
            meta = dict(re.findall(r"^(FILE|PAGE|LINES|PURPOSE): (.*)$", full, re.M))
            body = _block(full)
            a, _, b = meta.get("LINES", "0-0").partition("-")
            sel = {"kind": "select", "file": meta.get("FILE"),
                   "page": int(meta.get("PAGE", 0) or 0),
                   "start": int(a or 0), "end": int(b or a or 0),
                   "purpose": meta.get("PURPOSE", ""), "text": body,
                   "lines": list(_ref_lines(body)), "ts": ts}
            ev.append(sel)
            run["selected"].append(sel)
        elif head.startswith("FINAL ANSWER"):
            run["answer"] = _block(full)
            ev.append({"kind": "final", "ts": ts})

    for r in runs:
        for i, e in enumerate(r["events"]):
            e["idx"] = i
    return [r for r in runs if r["answer"] is not None]


# ============================================================
# 2. CLAIMS AND CITATIONS
# ============================================================

CIT_RE = re.compile(r"[\[【(]([^\[\]【】()]*?P\s*\d+\s*-\s*L\s*\d+[^\[\]【】()]*)[\]】)]")
REF_RE = re.compile(
    r"P\s*(\d+)\s*-\s*L\s*(\d+)(?:\s*(?:[-–:]|to)\s*(?:P\s*\d+\s*-\s*)?L?\s*(\d+))?")
FILE_RE = re.compile(r"([\w.\- ]+?\.pdf)", re.I)


def _norm(text):
    return (text.replace("‑", "-").replace("‐", "-").replace("–", "-")
            .replace("—", " ").replace(" ", " ").replace(" ", " "))


def parse_citations(text, default_file=None):
    cites = []
    for m in CIT_RE.finditer(text):
        inner = m.group(1)
        fm = FILE_RE.search(inner)
        filename = fm.group(1).strip() if fm else default_file
        for r in REF_RE.finditer(inner):
            page, start = int(r.group(1)), int(r.group(2))
            end = int(r.group(3)) if r.group(3) else start
            if end < start:
                end = start
            cites.append({"file": filename, "page": page, "start": start,
                          "end": min(end, start + 200), "raw": m.group(0)})
    return cites


def split_claims(answer, default_file=None):
    """One claim per bullet / line; split long lines into cited sentences."""
    claims = []
    for line in _norm(answer).splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if not line:
            continue
        # split a paragraph into sentences, keeping trailing citations attached
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", line)
        for part in parts:
            cites = parse_citations(part, default_file)
            body = CIT_RE.sub("", part).strip(" .;")
            body = re.sub(r"\s+", " ", body).strip()
            if not body:
                continue
            if not cites and (body.endswith(":") or len(body.split()) < 4):
                continue  # framing line, not a claim
            claims.append({"text": body, "raw": part, "citations": cites})
    for i, c in enumerate(claims, start=1):
        c["id"] = f"c{i}"
    return claims


# ============================================================
# 3. TERMS
# ============================================================

STOP = set("""
a an the and or but if then than that this these those there here is are was were be
been being have has had do does did of in on at to for from by with without as into
onto about over under via per across among between within your you yours i me my mine
we our us he she they them their it its which who whom whose what when where why how
all any both each few more most other some such no nor not only own same so too very
can will just should now also several ways way using use used uses shows show showing
demonstrates demonstrate demonstrating listed list known built build building developed
develop developing experience experiences evidence resume resumes résumé résumés based
give concise answer answers including include includes alongside hands-on hands
section sections project projects work worked part parts one two first second
indicates indicate indicating mentions mention mentioned stated states state says
clear clearly directly strong strongly further additionally also e.g i.e etc
""".split())

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[+#]+|(?:[.\-'][A-Za-z0-9]+)*)|\d+(?:[.,]\d+)*%?K?\+?")


def tokens(text):
    return TOKEN_RE.findall(_norm(text))


def question_terms(question):
    return sorted({t.lower() for t in tokens(question)
                   if t.lower() not in STOP and len(t) > 1})


def specific_terms(claim_text, q_terms):
    """Names, values and technologies in a claim, plus the question focus."""
    out = []
    for i, t in enumerate(tokens(claim_text)):
        low = t.lower()
        if low in STOP or len(t) < 2 and not t.isdigit():
            continue
        if (any(ch.isupper() for ch in t) or any(ch.isdigit() for ch in t)
                or low in q_terms or "+" in t or "#" in t):
            if low not in out:
                out.append(low)
    return out


def contains(text, term):
    pat = rf"(?<![\w+#]){re.escape(term)}(?:e?s)?(?![\w+#])"
    return re.search(pat, _norm(text), re.I) is not None


# ============================================================
# 4. ANALYSIS
# ============================================================

def _seen_map(run):
    """(file, page, line) -> {text, first_idx, modes} for text shown by tools."""
    seen = {}
    for e in run["events"]:
        if e["kind"] != "tool_result":
            continue
        for p, l, t in e["lines"]:
            key = (e["file"], p, l)
            rec = seen.setdefault(key, {"text": t, "first_idx": e["idx"], "modes": set()})
            rec["modes"].add(e["mode"])
    return seen


def _own_text_events(run):
    """Agent-authored text: reasoning/messages and tool arguments."""
    for e in run["events"]:
        if e["kind"] == "agent_text":
            yield e["idx"], e["text"], e["source"]
        elif e["kind"] == "tool_request":
            args = {k: v for k, v in e["args"].items()
                    if k in ("query", "_raw", "purpose")}
            if args:
                yield e["idx"], " ".join(str(v) for v in args.values()), f"tool:{e['tool']}"


def _first_evidence_idx(run, term):
    for e in run["events"]:
        if e["kind"] == "tool_result" and contains(e["text"], term):
            return e["idx"]
    return None


def _step_labels(run):
    """Human labels for the timeline (one per tool call + answer)."""
    steps = []
    for e in run["events"]:
        if e["kind"] == "tool_request":
            a = e["args"]
            if e["tool"] == "search_pdf":
                lab = f'search "{a.get("query", "")}"'
            elif e["tool"] == "read_pdf_pages":
                s, t = a.get("start_page"), a.get("end_page")
                lab = f"read p{s}" + (f"-{t}" if t != s else "")
            elif e["tool"] == "select_evidence":
                lab = f'select p{a.get("page")} L{a.get("start_line")}-{a.get("end_line")}'
            elif e["tool"] == "list_files":
                lab = "list files"
            else:
                lab = e["tool"]
            steps.append({"idx": e["idx"], "tool": e["tool"], "label": lab})
        elif e["kind"] == "agent_text":
            steps.append({"idx": e["idx"], "tool": "think", "label": "agent text"})
        elif e["kind"] == "final":
            steps.append({"idx": e["idx"], "tool": "answer", "label": "final answer"})
    return steps


def _step_of(steps, idx):
    """Map an event index to the step whose window contains it."""
    if idx is None:
        return None
    best = None
    for n, s in enumerate(steps):
        if s["idx"] <= idx:
            best = n
    return best


def analyse_claim(claim, run, seen, q_terms, steps):
    files_seen = {k[0] for k in seen}
    default_file = next(iter(files_seen)) if len(files_seen) == 1 else None
    selected = {(s["file"], s["page"], l) for s in run["selected"]
                for l in range(s["start"], s["end"] + 1)}
    select_text = {(s["file"], p, l): t for s in run["selected"] for p, l, t in s["lines"]}

    # ---- cited lines + grounding ---------------------------------------
    cited = []
    for c in claim["citations"]:
        f = c["file"] or default_file
        for l in range(c["start"], c["end"] + 1):
            key = (f, c["page"], l)
            if any(x["key"] == key for x in cited):
                continue
            s = seen.get(key)
            if s:
                status = "opened" if "read" in s["modes"] else "snippet only"
                text = s["text"]
            elif key in select_text:
                status, text = "selected, never shown", select_text[key]
            else:
                status, text = "never seen", ""
            cited.append({"key": key, "file": f, "page": c["page"], "line": l,
                          "text": text, "status": status,
                          "selected": key in selected})

    terms = specific_terms(claim["text"], q_terms)
    cited_text = "\n".join(x["text"] for x in cited)
    other_seen = "\n".join(v["text"] for k, v in seen.items()
                           if k not in {x["key"] for x in cited})

    # ---- term support tiers ---------------------------------------------
    term_rows = []
    for t in terms:
        if contains(cited_text, t):
            where = "cited"
        elif contains(other_seen, t):
            where = "elsewhere in sources"
        else:
            where = "not in any source"
        # timing signal (skip terms the question itself supplied)
        own_idx, own_src = None, None
        if t not in q_terms:
            for idx, txt, src in _own_text_events(run):
                if contains(txt, t):
                    own_idx, own_src = idx, src
                    break
        ev_idx = _first_evidence_idx(run, t)
        early = own_idx is not None and (ev_idx is None or own_idx < ev_idx)
        term_rows.append({"term": t, "support": where, "in_question": t in q_terms,
                          "first_own_step": _step_of(steps, own_idx),
                          "first_own_source": own_src,
                          "first_evidence_step": _step_of(steps, ev_idx),
                          "asserted_before_seen": early})

    # ---- jenga pull: leave-one-out over cited lines ---------------------
    for x in cited:
        x["terms"] = [t for t in terms if contains(x["text"], t)]
    for x in cited:
        others = set().union(*[set(y["terms"]) for y in cited if y is not x]) if len(cited) > 1 else set()
        x["unique_terms"] = [t for t in x["terms"] if t not in others]
        x["role"] = ("load-bearing" if x["unique_terms"] else
                     "supporting" if x["terms"] else
                     "phantom" if x["status"] in ("never seen", "selected, never shown") else
                     "dead weight")

    n_terms = len(terms)
    covered = [r for r in term_rows if r["support"] == "cited"]
    e_lex = round(len(covered) / n_terms, 2) if n_terms else None
    # terms found just outside the cited span (e.g. a section header) count half
    e_eff = ((len(covered) + 0.5 * sum(1 for r in term_rows
                                       if r["support"] == "elsewhere in sources")) / n_terms
             if n_terms else None)
    precision = (round(sum(1 for x in cited if x["terms"]) / len(cited), 2)
                 if cited else None)
    bridging = [r["term"] for r in term_rows
                if r["in_question"] and r["support"] != "cited"]
    timing = [r["term"] for r in term_rows if r["asserted_before_seen"]]
    phantom = [x for x in cited if x["status"] in ("never seen", "selected, never shown")]
    not_selected = [x for x in cited if not x["selected"]]
    outside = [r["term"] for r in term_rows
               if r["support"] == "elsewhere in sources" and r["term"] not in bridging]
    prior = [r["term"] for r in term_rows if r["support"] == "not in any source"]

    # ---- verdict + transparent score -------------------------------------
    deductions = []
    if not claim["citations"]:
        verdict = "uncited"
        deductions.append(("No citation: the claim cannot be traced to a source", 50))
    if phantom:
        deductions.append((f"{len(phantom)} cited line(s) were never shown to the agent", 50))
    if timing:
        deductions.append((f"Asserted before any evidence contained it: {', '.join(timing)}", 40))
    if bridging:
        deductions.append((f"Question focus not in the cited lines: {', '.join(bridging)} "
                           "(link supplied by the model's prior)", 25))
    if prior:
        deductions.append((f"Not found in any source the agent saw: {', '.join(prior)}",
                           min(30, 10 * len(prior))))
    if outside:
        deductions.append((f"Only supported outside the citation: {', '.join(outside)}",
                           min(15, 5 * len(outside))))
    if precision is not None and precision < 1:
        dead = sum(1 for x in cited if x["role"] == "dead weight")
        if dead:
            deductions.append((f"{dead} cited line(s) are dead weight", round((1 - precision) * 20)))
    if not_selected and run["selected"]:
        deductions.append((f"{len(not_selected)} cited line(s) were not selected as evidence", 5))
    score = max(0, 100 - sum(d for _, d in deductions))

    if not claim["citations"]:
        verdict = "uncited"
    elif phantom:
        verdict = "phantom citation"
    elif timing:
        verdict = "post-rationalised"
    elif bridging or prior or (e_eff is not None and e_eff < 0.5):
        verdict = "post-rationalisation risk"
    else:
        verdict = "grounded"

    return {
        "id": claim["id"], "text": claim["text"], "citations": claim["citations"],
        "verdict": verdict, "score": score,
        "deductions": [{"reason": r, "points": p} for r, p in deductions],
        "E_lex": e_lex, "precision": precision, "terms": term_rows,
        "bridging_terms": bridging, "timing_terms": timing,
        "cited_lines": [{k: v for k, v in x.items() if k != "key"} for x in cited],
        "causal": None,
    }


def analyse_run(run):
    seen = _seen_map(run)
    q_terms = question_terms(run["question"])
    steps = _step_labels(run)
    files = sorted({k[0] for k in seen if k[0]})
    default_file = files[0] if len(files) == 1 else None
    claims = [analyse_claim(c, run, seen, q_terms, steps)
              for c in split_claims(run["answer"], default_file)]

    verdicts = {}
    for c in claims:
        verdicts[c["verdict"]] = verdicts.get(c["verdict"], 0) + 1
    trust = round(sum(c["score"] for c in claims) / len(claims)) if claims else None

    # term first-appearance for the timeline
    term_marks = {}
    for c in claims:
        for t in c["terms"]:
            term_marks.setdefault(t["term"], {
                "evidence_step": t["first_evidence_step"],
                "own_step": t["first_own_step"],
                "early": t["asserted_before_seen"]})

    notes = []
    if not run["has_agent_text"]:
        notes.append("This log does not record the agent's own reasoning or messages, so "
                     "the timing signal only sees tool arguments (e.g. search queries). "
                     "Use the instrumented agent4.py to log them.")
    notes.append("Static signals are lexical. They show whether the cited text can carry "
                 "the claim, not whether the agent causally relied on it; run the replay "
                 "(--replay) for causal labels.")

    return {
        "run_id": run["run_id"] or run["started"], "question": run["question"],
        "model": run["model"], "label": run["label"], "answer": run["answer"],
        "question_terms": q_terms, "files": files,
        "steps": steps, "term_marks": term_marks,
        "selected_evidence": [{k: s[k] for k in ("file", "page", "start", "end", "purpose")}
                              for s in run["selected"]],
        "claims": claims, "verdict_counts": verdicts, "trust_score": trust,
        "trace_has_agent_text": run["has_agent_text"], "notes": notes,
    }


def analyse_log_text(text):
    return [analyse_run(r) for r in parse_log(text)]


# ============================================================
# 5. INFOGRAPHIC (self-contained HTML + inline SVG)
# ============================================================

def esc(x):
    return _html.escape(str(x), quote=True)


VERDICT_STYLE = {
    "grounded": ("ok", "Grounded"),
    "post-rationalisation risk": ("warn", "Post-rationalisation risk"),
    "post-rationalised": ("bad", "Post-rationalised"),
    "phantom citation": ("bad", "Phantom citation"),
    "uncited": ("muted", "Uncited"),
}

CSS = """
:root{--bg:#f7f6f3;--card:#fff;--ink:#1d1d1f;--sub:#5f6368;--line:#e3e1dc;
--ok:#1a7f5a;--ok-bg:#e3f4ec;--warn:#a15c00;--warn-bg:#fdf0dc;--bad:#b3261e;--bad-bg:#fbe5e3;
--muted:#6b6b6b;--muted-bg:#ececec;--acc:#3b5bdb;--acc2:#9aaef0;--dead:#d6d3cc}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141416;--card:#1f1f23;
--ink:#ececf0;--sub:#a3a3ad;--line:#33333a;--ok:#5fd3a2;--ok-bg:#16352a;--warn:#f0b35c;
--warn-bg:#3a2a12;--bad:#ff8a80;--bad-bg:#3d1c1a;--muted:#b0b0b0;--muted-bg:#2c2c30;
--acc:#8ea6ff;--acc2:#4a5a99;--dead:#3c3a36}}
:root[data-theme="dark"]{--bg:#141416;--card:#1f1f23;--ink:#ececf0;--sub:#a3a3ad;--line:#33333a;
--ok:#5fd3a2;--ok-bg:#16352a;--warn:#f0b35c;--warn-bg:#3a2a12;--bad:#ff8a80;--bad-bg:#3d1c1a;
--muted:#b0b0b0;--muted-bg:#2c2c30;--acc:#8ea6ff;--acc2:#4a5a99;--dead:#3c3a36}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}
.sub{color:var(--sub);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
.hero{display:grid;grid-template-columns:170px 1fr;gap:22px;align-items:center}
@media (max-width:640px){.hero{grid-template-columns:1fr}.claim{grid-template-columns:1fr!important}}
.q{font-size:17px;font-weight:600;margin:4px 0 10px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{border-radius:999px;padding:3px 10px;font-size:12.5px;font-weight:600;white-space:nowrap}
.ok{color:var(--ok);background:var(--ok-bg)}.warn{color:var(--warn);background:var(--warn-bg)}
.bad{color:var(--bad);background:var(--bad-bg)}.muted{color:var(--muted);background:var(--muted-bg)}
.timeline{overflow-x:auto;padding-bottom:6px}
.claim{display:grid;grid-template-columns:230px 1fr;gap:20px;margin-top:14px}
.ctext{font-size:15.5px;font-weight:600;margin:6px 0 10px}
.metric{display:grid;grid-template-columns:150px 1fr 44px;gap:8px;align-items:center;
font-size:13px;margin:5px 0}
.bar{height:9px;background:var(--muted-bg);border-radius:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--acc);border-radius:6px}
ul.ded{margin:8px 0 0;padding-left:18px;font-size:13px;color:var(--sub)}
.term{border:1px solid var(--line);border-radius:7px;padding:2px 8px;font-size:12.5px}
.t-cited{border-color:var(--ok);color:var(--ok)}.t-else{border-color:var(--warn);color:var(--warn)}
.t-none{border-color:var(--bad);color:var(--bad)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--sub);margin-top:8px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:5px}
.notes{font-size:13px;color:var(--sub)}
details summary{cursor:pointer;color:var(--sub);font-size:13px;margin-top:10px}
pre{white-space:pre-wrap;font-size:12.5px;background:var(--bg);padding:10px;border-radius:8px}
.causal{margin-top:10px;border-top:1px dashed var(--line);padding-top:10px;font-size:13px}
"""


def _donut(score, size=150):
    if score is None:
        score = 0
    r, c = 58, 2 * math.pi * 58
    cls = "ok" if score >= 75 else "warn" if score >= 45 else "bad"
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 150 150" role="img" '
            f'aria-label="Trust score {score} out of 100">'
            f'<circle cx="75" cy="75" r="{r}" fill="none" stroke="var(--muted-bg)" stroke-width="14"/>'
            f'<circle cx="75" cy="75" r="{r}" fill="none" stroke="var(--{cls})" stroke-width="14" '
            f'stroke-linecap="round" stroke-dasharray="{c * score / 100:.1f} {c:.1f}" '
            f'transform="rotate(-90 75 75)"/>'
            f'<text x="75" y="80" text-anchor="middle" font-size="34" font-weight="700" '
            f'fill="var(--ink)">{score}</text>'
            f'<text x="75" y="102" text-anchor="middle" font-size="11" fill="var(--sub)">'
            f'trust / 100</text></svg>')


def _timeline(rep):
    steps = [s for s in rep["steps"] if s["tool"] != "think"] or rep["steps"]
    if not steps:
        return ""
    idx_to_pos = {}
    full = rep["steps"]
    for n, s in enumerate(full):
        # map every step (incl. think) to the nearest visible step at or after it
        vis = next((k for k, v in enumerate(steps) if v["idx"] >= s["idx"]), len(steps) - 1)
        idx_to_pos[n] = vis
    gap, x0 = 128, 110
    w = max(560, x0 + gap * (len(steps) - 1) + 80)
    marks = rep["term_marks"]
    rows = [(t, m) for t, m in marks.items()
            if m["evidence_step"] is not None or m["own_step"] is not None]
    h = 110 + 22 * len(rows)
    color = {"search_pdf": "var(--acc)", "read_pdf_pages": "var(--acc)",
             "select_evidence": "var(--ok)", "answer": "var(--ink)"}
    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="Timeline of agent steps">',
             f'<line x1="{x0}" y1="40" x2="{x0 + gap * (len(steps) - 1)}" y2="40" '
             f'stroke="var(--line)" stroke-width="3"/>']
    for k, s in enumerate(steps):
        x = x0 + gap * k
        parts.append(f'<circle cx="{x}" cy="40" r="9" fill="{color.get(s["tool"], "var(--sub)")}"/>')
        parts.append(f'<text x="{x}" y="18" text-anchor="middle" font-size="11" '
                     f'fill="var(--sub)">step {k + 1}</text>')
        parts.append(f'<text x="{x}" y="68" text-anchor="middle" font-size="11.5" '
                     f'fill="var(--ink)">{esc(s["label"][:22])}</text>')
    y = 100
    for t, m in rows:
        parts.append(f'<text x="4" y="{y + 4}" font-size="11.5" fill="var(--sub)">{esc(t[:14])}</text>')
        if m["evidence_step"] is not None:
            x = x0 + gap * idx_to_pos.get(m["evidence_step"], 0)
            parts.append(f'<rect x="{x - 6}" y="{y - 6}" width="12" height="12" rx="3" '
                         f'fill="var(--ok)"><title>first seen in a tool result</title></rect>')
        if m["own_step"] is not None:
            x = x0 + gap * idx_to_pos.get(m["own_step"], 0)
            col = "var(--bad)" if m["early"] else "var(--acc2)"
            parts.append(f'<circle cx="{x + (14 if m["evidence_step"] == m["own_step"] else 0)}" '
                         f'cy="{y}" r="6" fill="none" stroke="{col}" stroke-width="2.5">'
                         f'<title>first written by the agent</title></circle>')
        y += 22
    parts.append("</svg>")
    legend = ('<div class="legend"><span><i class="sw" style="background:var(--ok)"></i>'
              'value first appears in evidence</span><span><i class="sw" style="border:2.5px '
              'solid var(--acc2);border-radius:50%"></i>agent writes it after seeing it</span>'
              '<span><i class="sw" style="border:2.5px solid var(--bad);border-radius:50%"></i>'
              'agent writes it BEFORE any evidence (searching to confirm)</span></div>')
    return f'<div class="timeline">{"".join(parts)}</div>{legend}'


def _tower(claim):
    """Jenga tower: one block per cited line, bottom = first cited line."""
    lines = claim["cited_lines"]
    if not lines:
        return ('<svg width="210" height="90" role="img" aria-label="No citations">'
                '<rect x="20" y="60" width="170" height="10" rx="2" fill="var(--line)"/>'
                '<text x="105" y="45" text-anchor="middle" font-size="12" fill="var(--sub)">'
                'no blocks: uncited</text></svg>')
    bh, gap = 30, 5
    shown = lines[:9]
    h = 30 + len(shown) * (bh + gap)
    out = [f'<svg width="210" height="{h}" viewBox="0 0 210 {h}" role="img" '
           f'aria-label="Citation tower">']
    style = {"load-bearing": ('fill="var(--acc)"', "var(--card)"),
             "supporting": ('fill="var(--acc2)"', "var(--ink)"),
             "dead weight": ('fill="var(--dead)"', "var(--sub)"),
             "phantom": ('fill="none" stroke="var(--bad)" stroke-width="2" '
                         'stroke-dasharray="5 4"', "var(--bad)")}
    for n, x in enumerate(shown):
        y = h - 12 - (n + 1) * (bh + gap) + gap
        off = (n % 2) * 8
        attrs, tc = style[x["role"]]
        tip = f'P{x["page"]}-L{x["line"]} ({x["role"]}, {x["status"]}): {x["text"]}'
        out.append(f'<g><title>{esc(tip)}</title><rect x="{12 + off}" y="{y}" width="178" '
                   f'height="{bh}" rx="4" {attrs}/>'
                   f'<text x="{22 + off}" y="{y + 19}" font-size="11" fill="{tc}" '
                   f'font-weight="600">L{x["line"]}</text>'
                   f'<text x="{50 + off}" y="{y + 19}" font-size="10.5" fill="{tc}">'
                   f'{esc((x["text"] or "(not shown)")[:24])}</text></g>')
    out.append(f'<rect x="4" y="{h - 10}" width="202" height="6" rx="3" fill="var(--line)"/>')
    if len(lines) > len(shown):
        out.append(f'<text x="105" y="12" text-anchor="middle" font-size="11" '
                   f'fill="var(--sub)">+{len(lines) - len(shown)} more</text>')
    out.append("</svg>")
    return "".join(out)


def _bar(label, value, hint=""):
    if value is None:
        return (f'<div class="metric"><span title="{esc(hint)}">{esc(label)}</span>'
                f'<span class="sub">n/a</span><span></span></div>')
    return (f'<div class="metric"><span title="{esc(hint)}">{esc(label)}</span>'
            f'<div class="bar"><i style="width:{value * 100:.0f}%"></i></div>'
            f'<b>{value:.2f}</b></div>')


def _causal_html(c):
    k = c.get("causal")
    if not k:
        return ""
    rows = []
    if k.get("E") is not None:
        rows.append(_bar("E (causal share)", k["E"],
                         "Drop rate of the claim when all cited spans are removed"))
    if k.get("F") is not None:
        rows.append(_bar("F (faithfulness)", k["F"],
                         "Share of cited spans whose removal changes the claim"))
    for s in k.get("removals", []):
        outs = ", ".join(sorted(set(s.get("outcomes", [])))) or (
            "claim survives" if s["survived"] else "claim drops")
        cites = f' (answer cites {esc(", ".join(s["new_cites"]))})' if s.get("new_cites") else ""
        rows.append(f'<div class="sub">remove {esc(s["span"])}: {esc(outs)}{cites}</div>')
    if k.get("plant"):
        p = k["plant"]
        cls = {"follows": "ok", "abstains": "ok", "overrides and cites": "bad",
               "overrides without citing": "warn"}.get(p["outcome"], "muted")
        rows.append(f'<div style="margin-top:6px">Planted <b>{esc(p["original"])}</b> &rarr; '
                    f'<b>{esc(p["planted"])}</b>: <span class="chip {cls}">'
                    f'{esc(p["outcome"])}</span></div>')
    return f'<div class="causal"><b>Causal replay</b>{"".join(rows)}</div>'


def _claim_card(c):
    cls, name = VERDICT_STYLE.get(c["verdict"], ("muted", c["verdict"]))
    if c.get("causal") and c["causal"].get("label"):
        lab = c["causal"]["label"]
        cls = "bad" if lab == "post-rationalised" else "ok" if lab == "faithful" else cls
        name = f"{name} · replay: {lab}"
    cites = ", ".join(sorted({f'P{x["page"]}-L{x["line"]}' for x in c["cited_lines"]},
                             key=lambda s: tuple(int(n) for n in re.findall(r"\d+", s))))
    tclass = {"cited": "t-cited", "elsewhere in sources": "t-else", "not in any source": "t-none"}
    terms = "".join(f'<span class="term {tclass[t["support"]]}" title="{esc(t["support"])}">'
                    f'{esc(t["term"])}{" ⏱" if t["asserted_before_seen"] else ""}</span>'
                    for t in c["terms"]) or '<span class="sub">no specific terms</span>'
    ded = "".join(f"<li>{'&minus;' + str(d['points']) + ': ' if d['points'] else ''}"
                  f"{esc(d['reason'])}</li>" for d in c["deductions"])
    lines = "\n".join(f'P{x["page"]}-L{x["line"]} [{x["role"]}; {x["status"]}] {x["text"]}'
                      for x in c["cited_lines"])
    return f"""
<div class="card claim">
  <div>{_tower(c)}</div>
  <div>
    <div class="chips"><span class="chip {cls}">{esc(name)}</span>
      <span class="chip muted">score {c['score']}</span>
      <span class="chip muted">{esc(cites or 'no citation')}</span></div>
    <div class="ctext">{esc(c['text'])}</div>
    <div class="chips" style="margin-bottom:8px">{terms}</div>
    {_bar("Evidence share (E_lex)", c["E_lex"], "Share of the claim's specific terms found in the cited lines")}
    {_bar("Citation precision", c["precision"], "Share of cited lines that contribute any claim term")}
    {f'<ul class="ded">{ded}</ul>' if ded else '<div class="sub" style="margin-top:6px">No issues found.</div>'}
    {_causal_html(c)}
    <details><summary>Cited lines</summary><pre>{esc(lines or '(none)')}</pre></details>
  </div>
</div>"""


def _run_section(rep, n_runs):
    counts = "".join(f'<span class="chip {VERDICT_STYLE.get(v, ("muted",))[0]}">{k} '
                     f'{esc(VERDICT_STYLE.get(v, (None, v))[1])}</span>'
                     for v, k in sorted(rep["verdict_counts"].items()))
    notes = "".join(f"<li>{esc(n)}</li>" for n in rep["notes"])
    title = f'<h2>Run {esc(rep["run_id"])}{" · " + esc(rep["label"]) if rep.get("label") else ""}</h2>' if n_runs > 1 else ""
    return f"""
{title}
<div class="card hero">
  <div>{_donut(rep['trust_score'])}</div>
  <div>
    <div class="sub">Question · {esc(rep['model'] or 'unknown model')} · {len(rep['claims'])} claims</div>
    <div class="q">{esc(rep['question'])}</div>
    <div class="chips">{counts}</div>
  </div>
</div>
<h2>How the agent got there</h2>
<div class="card">{_timeline(rep)}</div>
<h2>Claims: pull each block, see if the tower stands</h2>
<div class="legend">
  <span><i class="sw" style="background:var(--acc)"></i>load-bearing line (only source of a claim term)</span>
  <span><i class="sw" style="background:var(--acc2)"></i>supporting (term also in another cited line)</span>
  <span><i class="sw" style="background:var(--dead)"></i>dead weight (cited, supplies nothing)</span>
  <span><i class="sw" style="border:2px dashed var(--bad)"></i>phantom (never shown to agent)</span>
</div>
<div class="legend">
  <span><span class="term t-cited">term</span> in cited lines</span>
  <span><span class="term t-else">term</span> only elsewhere in sources</span>
  <span><span class="term t-none">term</span> in no source (model prior)</span>
  <span>⏱ written by the agent before any evidence contained it</span>
</div>
{''.join(_claim_card(c) for c in rep['claims'])}
<h2>Final answer as given</h2>
<div class="card"><pre>{esc(rep['answer'])}</pre></div>
<ul class="notes">{notes}</ul>
"""


def render_html(reports, title="Jenga evidence report"):
    if isinstance(reports, dict):
        reports = [reports]
    body = "".join(_run_section(r, len(reports)) for r in reports) or \
        '<div class="card">No completed runs found in this log.</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{esc(title)}</h1>
<div class="sub">Post-rationalisation check: does each citation carry its claim, and did the agent use it?</div>
{body}
</div></body></html>"""


# ============================================================
# 6. OUTPUT FILES (project interface: claims.jsonl, jenga.jsonl, report.json)
# ============================================================

def write_outputs(reports, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "claims.jsonl", "w", encoding="utf-8") as fc, \
         open(out / "jenga.jsonl", "w", encoding="utf-8") as fj:
        for r in reports:
            for c in r["claims"]:
                cid = f'{r["run_id"]}:{c["id"]}'
                fc.write(json.dumps({"claim_id": cid, "run_id": r["run_id"], "text": c["text"],
                                     "citations": c["citations"],
                                     "values": [t["term"] for t in c["terms"]]},
                                    ensure_ascii=False) + "\n")
                fj.write(json.dumps({"claim_id": cid, "verdict": c["verdict"],
                                     "score": c["score"], "E_lex": c["E_lex"],
                                     "precision": c["precision"],
                                     "timing_flag": bool(c["timing_terms"]),
                                     "bridging_terms": c["bridging_terms"],
                                     "causal": c["causal"]}, ensure_ascii=False) + "\n")
    with open(out / "report.json", "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    (out / "infographic.html").write_text(render_html(reports), encoding="utf-8")
    return out / "infographic.html"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Jenga post-rationalisation analysis of agent logs")
    ap.add_argument("logs", nargs="+", help="agent log file(s)")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--replay", action="store_true",
                    help="run causal source-removal and planted-value replays (needs GROQ_API_KEY)")
    ap.add_argument("--files-dir", default=None, help="PDF folder for replays (default: ./ai_files)")
    ap.add_argument("--plant", action="append", default=[],
                    help="override a planted value, e.g. --plant Python=Ruby")
    ap.add_argument("--samples", type=int, default=1, help="replay runs per condition")
    a = ap.parse_args(argv)

    reports = []
    for p in a.logs:
        reports += analyse_log_text(Path(p).read_text(encoding="utf-8"))
    if not reports:
        sys.exit("No completed runs found.")

    if a.replay:
        from replay import replay_report
        plants = dict(x.split("=", 1) for x in a.plant)
        for r in reports:
            replay_report(r, files_dir=a.files_dir, plant_overrides=plants, samples=a.samples)

    path = write_outputs(reports, a.out)
    for r in reports:
        print(f"\nRun {r['run_id']}  trust {r['trust_score']}/100  — {r['question']}")
        for c in r["claims"]:
            print(f"  [{c['score']:>3}] {c['verdict']:<26} {c['text'][:70]}")
            for d in c["deductions"]:
                print(f"         -{d['points']}: {d['reason']}")
    print(f"\nInfographic: {path}")


if __name__ == "__main__":
    main()
