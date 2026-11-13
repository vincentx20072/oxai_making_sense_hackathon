"""
View helpers for the web app: turn an agent log into display structures and
render the final answer with clickable citations.  No model calls here.
"""

import html
import re

from jenga import CIT_RE, REF_RE, FILE_RE, parse_log, _seen_map, _norm

TOOL_META = {
    "list_files": ("List files", "folder"),
    "get_pdf_info": ("PDF info", "info"),
    "read_pdf_pages": ("Read pages", "book"),
    "search_pdf": ("Search", "search"),
    "select_evidence": ("Select evidence", "pin"),
}


def latest_run(log_text, include_incomplete=True):
    runs = parse_log(log_text, include_incomplete=include_incomplete)
    return runs[-1] if runs else None


def cited_keys(answer, default_file=None):
    keys = set()
    for m in CIT_RE.finditer(_norm(answer or "")):
        inner = m.group(1)
        fm = FILE_RE.search(inner)
        f = fm.group(1).strip() if fm else default_file
        for r in REF_RE.finditer(inner):
            a = int(r.group(2))
            b = int(r.group(3)) if r.group(3) else a
            for l in range(a, min(max(a, b), a + 200) + 1):
                keys.add((f, int(r.group(1)), l))
    return keys


# ------------------------------------------------------------------
# Log -> turns
# ------------------------------------------------------------------

def _summarise_args(tool, args):
    if tool == "search_pdf":
        return f'"{args.get("query", "")}" in {args.get("filename", "?")}'
    if tool == "read_pdf_pages":
        a, b = args.get("start_page"), args.get("end_page")
        return f'{args.get("filename", "?")}, page {a}' + (f"–{b}" if b != a else "")
    if tool == "select_evidence":
        return (f'{args.get("filename", "?")}, p{args.get("page")} '
                f'L{args.get("start_line")}–{args.get("end_line")}')
    if tool == "get_pdf_info":
        return args.get("filename", "")
    return ""


def build_turns(run):
    """Group the run's events into one turn per model request."""
    if run is None:
        return [], set()
    files = sorted({e.get("file") for e in run["events"] if e.get("file")})
    cited = cited_keys(run["answer"], files[0] if len(files) == 1 else None)
    selected = {(s["file"], s["page"], l) for s in run["selected"]
                for l in range(s["start"], s["end"] + 1)}
    turns, cur, pending = [], None, None

    def new_turn(n):
        t = {"n": n, "items": []}
        turns.append(t)
        return t

    for e in run["events"]:
        k = e["kind"]
        if k == "request":
            cur = new_turn(e["n"])
            continue
        if cur is None:
            cur = new_turn(0)
        if k == "agent_text":
            cur["items"].append({"type": "think", "source": e["source"], "text": e["text"]})
        elif k == "tool_request":
            name, icon = TOOL_META.get(e["tool"], (e["tool"], "tool"))
            pending = {"type": "call", "tool": e["tool"], "name": name, "icon": icon,
                       "summary": _summarise_args(e["tool"], e["args"]),
                       "args": e["args"], "result": None}
            cur["items"].append(pending)
        elif k == "tool_result":
            res = {"mode": e["mode"], "file": e["file"], "text": e["text"],
                   "lines": [{"page": p, "line": l, "text": t,
                              "cited": (e["file"], p, l) in cited,
                              "selected": (e["file"], p, l) in selected}
                             for p, l, t in e["lines"]]}
            if pending is not None and pending["result"] is None:
                pending["result"] = res
            else:
                cur["items"].append({"type": "result", "result": res})
        elif k == "select":
            item = {"type": "select", "file": e["file"], "page": e["page"],
                    "start": e["start"], "end": e["end"], "purpose": e["purpose"],
                    "lines": [{"page": p, "line": l, "text": t,
                               "cited": (e["file"], p, l) in cited}
                              for p, l, t in e["lines"]]}
            # the select_evidence tool request precedes this; replace it with the richer card
            if cur["items"] and cur["items"][-1].get("tool") == "select_evidence":
                cur["items"][-1] = item
            else:
                cur["items"].append(item)
        elif k == "error":
            cur["items"].append({"type": "error", "text": e["text"]})
        elif k == "final":
            cur["items"].append({"type": "final", "text": run["answer"]})
    return turns, cited


def progress(run):
    """Short step list for the live progress view."""
    steps = []
    if run is None:
        return steps
    for e in run["events"]:
        if e["kind"] == "tool_request":
            name, _ = TOOL_META.get(e["tool"], (e["tool"], ""))
            steps.append(f"{name}: {_summarise_args(e['tool'], e['args'])}".rstrip(": "))
        elif e["kind"] == "final":
            steps.append("Wrote the final answer")
    return steps


def last_thought(run):
    if run is None:
        return ""
    for e in reversed(run["events"]):
        if e["kind"] == "agent_text":
            return e["text"][-400:]
    return ""


# ------------------------------------------------------------------
# Answer rendering
# ------------------------------------------------------------------

def _inline(text):
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def render_answer(answer, run, run_id, linkable=None):
    """Minimal markdown + citation chips that link to the PDF page."""
    if not answer:
        return ""
    seen = _seen_map(run) if run else {}
    files = sorted({k[0] for k in seen if k[0]})
    default_file = files[0] if len(files) == 1 else None
    chips = []

    def chip(m):
        inner = m.group(1)
        fm = FILE_RE.search(inner)
        f = fm.group(1).strip() if fm else default_file
        out = []
        for r in REF_RE.finditer(inner):
            p, a = int(r.group(1)), int(r.group(2))
            b = int(r.group(3)) if r.group(3) else a
            lines = [seen[(f, p, l)]["text"] for l in range(a, min(b, a + 40) + 1)
                     if (f, p, l) in seen]
            tip = "\n".join(lines) or "These lines were never shown to the agent"
            label = f"p{p} · L{a}" + (f"–{b}" if b != a else "")
            cls = "cite" if lines else "cite cite-missing"
            if f and (linkable is None or f in linkable):
                href = f"/runs/{run_id}/files/{html.escape(f)}#page={p}"
                out.append(f'<a class="{cls}" href="{href}" target="_blank" rel="noopener" '
                           f'title="{html.escape(tip)}">{html.escape(label)}</a>')
            else:
                out.append(f'<span class="{cls}" title="{html.escape(tip)}">{html.escape(label)}</span>')
        chips.append("".join(out))
        return f"\x00{len(chips) - 1}\x00"

    text = CIT_RE.sub(chip, _norm(answer))
    blocks, para, items = [], [], []

    def flush():
        if para:
            blocks.append("<p>" + " ".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            items.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        m = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", line)
        h = re.match(r"^#{1,4}\s+(.*)$", line)
        if h:
            flush()
            blocks.append(f"<h3>{_inline(h.group(1))}</h3>")
        elif m:
            if para:
                flush()
            items.append(m.group(1))
        else:
            if items:
                flush()
            para.append(line)
    flush()
    out = "\n".join(blocks)
    return re.sub(r"\x00(\d+)\x00", lambda m: chips[int(m.group(1))], out)
