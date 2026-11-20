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

Causal (replay.py, needs the source PDFs and a model API key).  NOT run by the
CLI or the web app, because each replay costs many API calls; call
replay.replay_report() yourself if you have the budget:
  * source removal: rerun with each cited span hidden, then all of them.
  * planted value:  rerun with the claim's value replaced in every document.
  These produce the plan's E and F and the replay labels
  (follows / overrides-and-cites / overrides-without-citing / abstains).

Usage
-----
  python jenga.py samples/agent4.log                 # writes report.json, claims.jsonl, jenga.jsonl

Library use (e.g. from the website):
  reports = analyse_log_text(text)          # list of report dicts, one per run
"""

import argparse
import json
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


def parse_log(text, include_incomplete=False):
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
        elif head.startswith(("TOOL ERROR", "INVALID TOOL ARGUMENTS")):
            ev.append({"kind": "error", "text": full, "ts": ts})
        elif head.startswith("FINAL ANSWER"):
            run["answer"] = _block(full)
            ev.append({"kind": "final", "ts": ts})

    for r in runs:
        for i, e in enumerate(r["events"]):
            e["idx"] = i
    return runs if include_incomplete else [r for r in runs if r["answer"] is not None]


# ============================================================
# 2. CLAIMS AND CITATIONS
# ============================================================

CIT_RE = re.compile(r"[\[【(]([^\[\]【】()]*?P\s*\d+\s*-\s*L\s*\d+[^\[\]【】()]*)[\]】)]")
REF_RE = re.compile(
    r"P\s*(\d+)\s*-\s*L\s*(\d+)(?:\s*(?:[-–:]|to)\s*(?:P\s*\d+\s*-\s*)?L?\s*(\d+))?")
FILE_RE = re.compile(r"([\w.\- ]+?\.pdf)", re.I)


def _norm(text):
    return (text.replace("‑", "-").replace("‐", "-").replace("–", "-")
            .replace("—", " ").replace("’", "'").replace(" ", " ").replace(" ", " "))


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
    """One claim per sentence.

    A citation covers the sentences before it in the same bullet / paragraph:
    an uncited sentence takes the citations of the next cited sentence on its
    line (marked "inherited").  Uncited sentences after the last citation stay
    uncited.  Headings, bold lead-ins and framing lines are skipped."""
    claims = []
    for line in _norm(answer).splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if not line or line.startswith("#"):
            continue
        if re.fullmatch(r"(\*\*|__)[^*_]+\1:?", line):
            continue  # a bold heading such as "**Gaps**"
        line_claims = []
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“*])|(?<=[.!?]\*\*)\s+", line):
            cites = parse_citations(part, default_file)
            body = CIT_RE.sub("", part)
            # markdown emphasis only: keep underscores inside words (file_name.pdf)
            body = re.sub(r"[*`]{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)", "", body).strip(" .;")
            body = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", body)).strip()
            if not body:
                continue
            if not cites and (body.endswith(":") or len(body.split()) < 4):
                continue  # framing line or bold lead-in, not a claim
            line_claims.append({"text": body, "raw": part, "citations": cites})
        nxt = []
        for c in reversed(line_claims):
            if c["citations"]:
                nxt = c["citations"]
            elif nxt:
                c["citations"] = [dict(x) for x in nxt]
                c["inherited"] = True
        claims.extend(line_claims)
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
cv cvs curriculum vitae document documents pdf pdfs file files
give concise answer answers including include includes alongside hands-on hands
section sections project projects work worked part parts one two first second
his her hers him himself herself theirs themselves itself conclusion conclusions overall
however therefore thus moreover summary note yes based appears appear seems likely
good fit strong solid core related practical relevant
indicates indicate indicating mentions mention mentioned stated states state says
clear clearly directly strong strongly further additionally also e.g i.e etc
""".split())

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[+#]+|(?:[.\-'][A-Za-z0-9]+)*)|\d+(?:[.,]\d+)*%?K?\+?")

# Descriptive words a summary wraps around the facts ("Proficient in Java",
# "Familiarity with Docker").  They are capitalised at the start of a bullet,
# but they are the answer's wording, not something a source should contain.
GENERIC = set("""
proficient proficiency familiar familiarity effective effectively extensive extensively
excellent exceptional skilled skill skills competent competence competency capable ability
abilities knowledge background mindset initiative expertise expert experienced proven
demonstrated routinely successfully various multiple diverse broad deep significant key
important impressive notable modern robust comprehensive familiarised familiarized
""".split())
# Word endings of adjectives / abstract nouns; only used for a capitalised
# word at the start of a phrase, where the capital comes from position alone.
GENERIC_SUFFIXES = ("ity", "ive", "ent", "ant", "ful", "ness", "ing", "ed", "ly", "ous")
PHRASE_START = set(".:;!?-–—•*([\"'")


def tokens(text):
    return TOKEN_RE.findall(_norm(text))


def question_terms(question):
    return sorted({t.lower() for t in tokens(question)
                   if t.lower() not in STOP and len(t) > 1})


# A judgement question asks for an opinion about something the documents
# cannot contain ("would this person be a good fit for Palantir?").  Its focus
# terms are the opinion's target, not facts the evidence should hold.
JUDGEMENT_RE = re.compile(
    r"\b(?:good|great|right|strong|suitable|ideal|best|bad|poor)\s+(?:fit|match|candidate|choice|option|hire)\b"
    r"|\b(?:do|would) you (?:think|say|recommend|consider)\b"
    r"|\bshould (?:i|we|they|he|she|this|the)\b"
    r"|\bis (?:this|it|he|she|they|the \w+) (?:a )?(?:good|worth|suitable|qualified|ready|right)\b"
    r"|\bhow (?:good|well|strong|suitable|likely)\b"
    r"|\b(?:recommend|suitab\w*|qualified|worth|assess|evaluate|rate)\b", re.I)


def question_kind(question):
    """'judgement' (asks for an opinion) or 'fact' (asks what the sources say)."""
    return "judgement" if JUDGEMENT_RE.search(_norm(question or "")) else "fact"


def _is_phrase_start(text, start):
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    return i < 0 or text[i] in PHRASE_START


def _soft_word(tok, phrase_start):
    """'Effective', 'Familiarity', 'Conducted' at the start of a bullet: likely
    capitalised by position only.  Kept as a term only if a source has it
    (so a real name like 'Spring' still counts)."""
    return (phrase_start and tok[:1].isupper() and tok[1:].islower()
            and len(tok) > 4 and tok.lower().endswith(GENERIC_SUFFIXES))


def specific_terms(claim_text, q_terms, with_labels=False):
    """Names, values and technologies in a claim, plus the question focus.

    with_labels=True returns (terms, {term: the claim's spelling}, soft terms)."""
    text = _norm(claim_text)
    out, labels, soft = [], {}, set()
    for m in TOKEN_RE.finditer(text):
        t = CONTRACTION_RE.sub("", m.group(0))  # "Palantir's" -> "Palantir", "He'd" -> "He"
        low = t.lower()
        if low in STOP or len(t) < 2 and not t.isdigit():
            continue
        if low in GENERIC and low not in q_terms:
            continue
        if (any(ch.isupper() for ch in t) or any(ch.isdigit() for ch in t)
                or low in q_terms or "+" in t or "#" in t):
            if low not in out:
                out.append(low)
                labels[low] = t
                if low not in q_terms and _soft_word(t, _is_phrase_start(text, m.start())):
                    soft.add(low)
            else:
                soft.discard(low)  # capitalised mid-sentence too: a real name
    return (out, labels, soft) if with_labels else out


CONTRACTION_RE = re.compile(r"'(?:s|d|ll|re|ve|m|t)$", re.I)


def _variants(term):
    """The term plus its singular ('apis' also matches 'API'); a round number
    also matches its K form ('2,000' matches '2K')."""
    num = re.fullmatch(r"(\d{1,3}(?:,\d{3})+|\d+)(\+?)", term)
    if num:
        n = int(num.group(1).replace(",", ""))
        v = [num.group(1), str(n)]
        if n >= 1000 and n % 1000 == 0:
            v.append(f"{n // 1000}k")
        return v
    kform = re.fullmatch(r"(\d+)k(\+?)", term, re.I)
    if kform:
        n = int(kform.group(1)) * 1000
        return [term.rstrip("+"), str(n), f"{n:,}"]
    v = [term]
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss") and term[-2].isalpha():
        v.append(term[:-1])
        if term.endswith("es"):
            v.append(term[:-2])
    return v


def contains(text, term):
    text = _norm(text)
    alts = "|".join(re.escape(v) for v in _variants(term))
    if term[:1].isdigit():   # numbers: "2K" matches "2K+", but not "2,500"
        if re.search(rf"(?<![\w,.])(?:{alts})\+?(?![\w#]|[.,]\d)", text, re.I):
            return True
    elif re.search(rf"(?<![\w+#])(?:{alts})(?:e?s)?(?![\w+#])", text, re.I):
        return True
    # a compound the answer coined: "Flask-React" is found if Flask and React are
    parts = [p for p in re.split(r"[-/]", term) if len(p) >= 2]
    return len(parts) >= 2 and all(contains(text, p) for p in parts)


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


def analyse_claim(claim, run, seen, q_terms, steps, subject=frozenset(), kind="fact"):
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

    raw, labels, soft = specific_terms(claim["text"], q_terms, with_labels=True)
    cited_text = "\n".join(x["text"] for x in cited)
    other_seen = "\n".join(v["text"] for k, v in seen.items()
                           if k not in {x["key"] for x in cited})
    # bullet-start wording ("Effective", "Familiarity") counts only if a source has it
    terms = [t for t in raw if t not in subject
             and not (t in soft and not contains(cited_text + "\n" + other_seen, t))]

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
        term_rows.append({"term": t, "label": labels.get(t, t), "support": where,
                          "in_question": t in q_terms,
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
    # On a judgement question the question's focus ("Palantir" in "a good fit
    # for Palantir?") is the opinion's target, which no document holds: not a
    # missing link.
    bridging = [r["term"] for r in term_rows
                if r["in_question"] and r["support"] != "cited" and kind != "judgement"]
    # "Said before seen" means the agent wrote a value before the documents
    # showed it.  A word no document contains (the model's own framing, e.g.
    # a job title it is assessing against) was never there to be seen early;
    # it is outside knowledge, counted under `prior`.
    timing = [r["term"] for r in term_rows
              if r["asserted_before_seen"] and r["support"] != "not in any source"]
    phantom = [x for x in cited if x["status"] in ("never seen", "selected, never shown")]
    not_selected = [x for x in cited if not x["selected"]]
    outside = [r["term"] for r in term_rows
               if r["support"] == "elsewhere in sources" and r["term"] not in bridging]
    prior = [r["term"] for r in term_rows if r["support"] == "not in any source"
             and not (kind == "judgement" and r["in_question"])]

    # ---- verdict + transparent score -------------------------------------
    deductions = []
    if not claim["citations"]:
        verdict = "uncited"
        deductions.append(("No citation: the claim cannot be traced to a source", 50))
    if phantom:
        deductions.append((f"{len(phantom)} cited line(s) were never shown to the agent", 50))
    # Timing: the agent wrote the value before any tool result contained it.
    # Naming a search target is common and only a weak signal on its own; it
    # becomes post-rationalisation when the citation also fails to carry the claim.
    weak_citation = bool(bridging or prior or (e_eff is not None and e_eff < 1))
    if timing:
        deductions.append((f"Written by the agent before any evidence contained it: "
                           f"{', '.join(timing)}", 40 if weak_citation else 20))
    uncited = not claim["citations"]
    if bridging and not uncited:
        deductions.append((f"Question focus not in the cited lines: {', '.join(bridging)} "
                           "(link supplied by the model's prior)", 25))
    if prior and not uncited:
        deductions.append((f"Not found in any source the agent saw: {', '.join(prior)}",
                           min(30, 10 * len(prior))))
    if outside and not uncited:
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
    elif timing and weak_citation:
        verdict = "post-rationalised"
    elif timing:
        verdict = "post-rationalisation risk"
    elif bridging or prior or (e_eff is not None and e_eff < 0.5):
        verdict = "post-rationalisation risk"
    else:
        verdict = "grounded"

    # ---- Alibi: was it there? (presence only, ignores timing) -------------
    # Checks the claim's keywords against the cited lines.  On a judgement
    # question the question's focus ("Palantir" in "a good fit for Palantir?")
    # is the opinion's target, not a fact the evidence could hold, so it is
    # left out.  On a fact question it is the fact asked for, so a citation
    # without it can't support the claim.
    #   phantom           a cited line was never shown to the agent
    #   supported         >= 75% of the keywords are in the cited lines
    #                     (a keyword just outside the span counts half)
    #   partly supported  >= 40%
    #   unsupported       no citation, less than that, or (fact question) the
    #                     question's focus is missing from the citation
    fact_rows = [r for r in term_rows if not (kind == "judgement" and r["in_question"])]
    alibi_terms = {
        "found": [r["label"] for r in fact_rows if r["support"] == "cited"],
        "nearby": [r["label"] for r in fact_rows if r["support"] == "elsewhere in sources"],
        "missing": [r["label"] for r in fact_rows if r["support"] == "not in any source"],
        "opinion": [r["label"] for r in term_rows if kind == "judgement" and r["in_question"]],
    }
    n_fact = len(fact_rows)
    alibi_share = (round((len(alibi_terms["found"]) + 0.5 * len(alibi_terms["nearby"])) / n_fact, 2)
                   if n_fact else None)
    if phantom:
        alibi = "phantom"
    elif not claim["citations"]:
        alibi = "unsupported"
    elif kind == "fact" and bridging:
        alibi = "unsupported"
    elif alibi_share is None or alibi_share >= 0.75:
        alibi = "supported"
    elif alibi_share >= 0.4:
        alibi = "partly supported"
    else:
        alibi = "unsupported"

    return {
        "id": claim["id"], "text": claim["text"], "citations": claim["citations"],
        "verdict": verdict, "score": score,
        "deductions": [{"reason": r, "points": p} for r, p in deductions],
        "E_lex": e_lex, "precision": precision, "terms": term_rows,
        "bridging_terms": bridging, "timing_terms": timing,
        "cited_lines": [{k: v for k, v in x.items() if k != "key"} for x in cited],
        "causal": None, "alibi": alibi, "alibi_terms": alibi_terms,
        "alibi_share": alibi_share,
    }


# Bump when the analysis changes; the web app re-analyses older reports.
REPORT_VERSION = 7


def analyse_run(run):
    seen = _seen_map(run)
    q_terms = question_terms(run["question"])
    # The question's subject (e.g. the person a resume is about) usually sits in a
    # document header, not in every cited line; don't treat it as a claim value.
    header = " ".join(v["text"] for (f, p, l), v in seen.items() if p == 1 and l <= 3)
    files = sorted({k[0] for k in seen if k[0]})
    # ...and a file's own name ("Christopher_Lee_CV.pdf") is not a claim value either
    file_words = {t.lower() for f in files for t in tokens(f.replace("_", " "))}
    subject = frozenset({t for t in q_terms if contains(header, t)} | file_words)
    kind = question_kind(run["question"])
    steps = _step_labels(run)
    default_file = files[0] if len(files) == 1 else None
    claims = [analyse_claim(c, run, seen, q_terms, steps, subject, kind)
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
                 "the claim, not whether the agent causally relied on it.")

    return {
        "run_id": run["run_id"] or run["started"], "question": run["question"],
        "model": run["model"], "label": run["label"], "answer": run["answer"],
        "question_terms": q_terms, "question_kind": kind, "files": files,
        "jenga_version": REPORT_VERSION,
        "steps": steps, "term_marks": term_marks,
        "selected_evidence": [{k: s[k] for k in ("file", "page", "start", "end", "purpose")}
                              for s in run["selected"]],
        "claims": claims, "verdict_counts": verdicts, "trust_score": trust,
        "trace_has_agent_text": run["has_agent_text"], "notes": notes,
    }


def analyse_log_text(text):
    return [analyse_run(r) for r in parse_log(text)]


# ============================================================
# 5. STYLES AND THE ALIBI x JENGA GRID (used by the web app)
# ============================================================

VERDICT_STYLE = {
    "grounded": ("ok", "Grounded"),
    "post-rationalisation risk": ("warn", "Post-rationalisation risk"),
    "post-rationalised": ("bad", "Post-rationalised"),
    "phantom citation": ("bad", "Phantom citation"),
    "uncited": ("muted", "Uncited"),
}

ALIBI_STYLE = {"supported": ("ok", "Alibi: supported"),
               "partly supported": ("warn", "Alibi: partly supported"),
               "unsupported": ("bad", "Alibi: unsupported"),
               "phantom": ("bad", "Alibi: phantom")}


def jenga_use(c):
    """Jenga axis of the 2x2: ("used" | "not used", source).  The replay label
    decides when it is conclusive; otherwise the static timing signal does."""
    lab = (c.get("causal") or {}).get("label")
    if lab in ("faithful", "redundant evidence"):   # redundant: the fact is cited twice
        return "used", "replay"
    if lab == "post-rationalised":
        return "not used", "replay"
    return ("not used" if c.get("timing_terms") else "used"), "static"


QUADRANTS = {
    ("supported", "used"): ("ok", "Faithful citation"),
    ("supported", "not used"): ("bad", "Cited but not used"),
    ("unsupported", "used"): ("warn", "Used but mis-cited"),
    ("unsupported", "not used"): ("bad", "Unsupported and unused"),
}


def alibi_2x2(claims):
    """Count claims into Alibi (supported / not) x Jenga (used / not used).
    Partly supported and phantom alibis count on the 'unsupported' row."""
    cells = {k: [] for k in QUADRANTS}
    for c in claims:
        a = c.get("alibi", "unsupported")
        cells[("supported" if a == "supported" else "unsupported", jenga_use(c)[0])].append(c)
    return cells


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
                                     "alibi": c.get("alibi"),
                                     "score": c["score"], "E_lex": c["E_lex"],
                                     "precision": c["precision"],
                                     "timing_flag": bool(c["timing_terms"]),
                                     "bridging_terms": c["bridging_terms"],
                                     "causal": c["causal"]}, ensure_ascii=False) + "\n")
    with open(out / "report.json", "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Jenga post-rationalisation analysis of agent logs")
    ap.add_argument("logs", nargs="+", help="agent log file(s)")
    ap.add_argument("--out", default="out", help="output directory")
    a = ap.parse_args(argv)

    reports = []
    for p in a.logs:
        reports += analyse_log_text(Path(p).read_text(encoding="utf-8"))
    if not reports:
        sys.exit("No completed runs found.")

    path = write_outputs(reports, a.out)
    for r in reports:
        print(f"\nRun {r['run_id']}  trust {r['trust_score']}/100  — {r['question']}")
        for c in r["claims"]:
            print(f"  [{c['score']:>3}] {c['verdict']:<26} {c.get('alibi', ''):<12} "
                  f"{c['text'][:60]}")
            for d in c["deductions"]:
                print(f"         -{d['points']}: {d['reason']}")
    print(f"\nOutputs: {path}")


if __name__ == "__main__":
    main()
