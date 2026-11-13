"""
PDF evidence agent (agent4), instrumented for Jenga analysis.

Changes from the original agent4.py:

* The model's own text (message content and, where the provider returns it,
  reasoning) is logged BEFORE every tool call.  Jenga's timing signal needs
  this: "did the value appear in the agent's own text before any evidence
  containing it?"  Without it the timing check can only look at tool
  arguments.
* Every step is also written to a structured trace (trace.jsonl) and the final
  answer to answer.json, following the project's frozen interface.
* Replay support for Jenga's causal tests:
    - hidden_lines: remove chosen extracted-text lines (source removal);
    - plants:       replace a value everywhere in the PDFs (planted value).
  Line numbers are kept stable when lines are hidden (the text is blanked,
  not deleted) so citations stay comparable across runs.
* No module-level mutable state, so several runs can execute in one process
  (needed by the website and the replay engine).

The human-readable log format is unchanged apart from the extra events, so
old and new logs both parse with jenga.py.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

# ============================================================
# CONFIGURATION
# ============================================================

MODEL = "openai/gpt-oss-120b"

BASE_DIR = Path(__file__).parent
FILES_DIR = BASE_DIR / "ai_files"
LOG_FILE = BASE_DIR / "agent4.log"
RUNS_DIR = BASE_DIR / "runs"

MAX_PAGES_PER_READ = 5
MAX_SEARCH_RESULTS = 30
MAX_ITERATIONS = 30

SYSTEM_PROMPT = (
    "You are a research assistant with access to "
    "a directory containing PDF documents.\n\n"
    "You do not know which PDFs exist until you use list_files.\n\n"
    "Use the provided tools to investigate the PDFs "
    "and answer the user's question.\n\n"
    "You can search PDF text with search_pdf and "
    "inspect complete pages with read_pdf_pages.\n\n"
    "Before each tool call, write one short sentence saying what you "
    "expect to find and why you are making the call.\n\n"
    "IMPORTANT EVIDENCE RULE:\n"
    "Before giving your final answer, you must use "
    "select_evidence to explicitly identify the "
    "passages that materially support your answer.\n\n"
    "Do not select an entire page unless the entire "
    "page genuinely matters. Prefer the smallest "
    "useful range of lines that supports the claim.\n\n"
    "If several different passages support different "
    "claims, call select_evidence separately for "
    "each relevant passage.\n\n"
    "Do not claim that a PDF contains information "
    "unless that information has actually been "
    "returned to you by one of the tools.\n\n"
    "In your final answer, put each claim on its own bullet and cite "
    "the selected evidence at the end of that bullet using references "
    "such as [filename, P2-L14:L18].\n\n"
    "Extracted PDF text may not perfectly represent "
    "visual layout, columns, images, charts, or "
    "other graphical content."
)


# ============================================================
# DOCUMENT STORE (handles replay perturbations)
# ============================================================

class DocumentStore:
    """
    Extracted PDF text, line-numbered, with optional perturbations.

    hidden_lines: {filename: {page: set(line_numbers)}}  -> text blanked
    plants:       {original_value: planted_value}       -> whole-word replace
    """

    def __init__(self, files_dir=FILES_DIR, hidden_lines=None, plants=None):
        self.files_dir = Path(files_dir)
        self.hidden_lines = hidden_lines or {}
        self.plants = plants or {}
        self._cache = {}

    def safe_path(self, filename):
        requested = (self.files_dir / filename).resolve()
        allowed = self.files_dir.resolve()
        if allowed not in requested.parents:
            raise ValueError("Access outside ai_files is not allowed.")
        if requested.suffix.lower() != ".pdf":
            raise ValueError("Only PDF files are supported.")
        if not requested.is_file():
            raise FileNotFoundError(f"File does not exist: {filename}")
        return requested

    def list_files(self):
        return sorted(
            p.name for p in self.files_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"
        )

    def pages(self, filename):
        """List of pages; each page is a list of (perturbed) lines."""
        if filename in self._cache:
            return self._cache[filename]
        reader = PdfReader(self.safe_path(filename))
        pages = []
        hidden = self.hidden_lines.get(filename, {})
        for page_no, page in enumerate(reader.pages, start=1):
            lines = (page.extract_text() or "").splitlines()
            out = []
            for line_no, text in enumerate(lines, start=1):
                if line_no in hidden.get(page_no, set()):
                    text = ""
                out.append(apply_plants(text, self.plants))
            pages.append(out)
        self._cache[filename] = pages
        return pages

    def size(self, filename):
        return self.safe_path(filename).stat().st_size


def apply_plants(text, plants):
    for original, planted in plants.items():
        text = re.sub(
            rf"(?<![\w+#]){re.escape(original)}(?![\w+#])",
            planted, text, flags=re.IGNORECASE,
        )
    return text


# ============================================================
# RUN RECORDER: human log + structured trace
# ============================================================

class Recorder:
    def __init__(self, log_file=LOG_FILE, runs_dir=RUNS_DIR, run_id=None,
                 write_files=True):
        self.log_file = Path(log_file) if log_file else None
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.run_dir = Path(runs_dir) / self.run_id if runs_dir else None
        self.write_files = write_files
        self.lines = []        # the log text of this run (also returned)
        self.trace = []        # structured steps
        if self.write_files and self.run_dir:
            self.run_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message):
        ts = datetime.now().isoformat(timespec="milliseconds")
        entry = f"[{ts}] {message}"
        self.lines.append(entry)
        if self.write_files and self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")

    def step(self, record):
        record = {"run_id": self.run_id, "step": len(self.trace) + 1, **record}
        self.trace.append(record)
        if self.write_files and self.run_dir:
            with open(self.run_dir / "trace.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def answer(self, record):
        if self.write_files and self.run_dir:
            with open(self.run_dir / "answer.json", "w", encoding="utf-8") as f:
                json.dump({"run_id": self.run_id, **record}, f,
                          ensure_ascii=False, indent=2)

    @property
    def text(self):
        return "\n".join(self.lines) + "\n"


def block(label, body, kind="RESULT"):
    return (f"{label}\n----- BEGIN {kind} -----\n{body}\n----- END {kind} -----")


# ============================================================
# TOOLS
# ============================================================

class Tools:
    def __init__(self, store, rec):
        self.store = store
        self.rec = rec
        self.selected_evidence = []

    # -- list_files ------------------------------------------
    def list_files(self):
        files = self.store.list_files()
        result = "\n".join(files) if files else "(No PDF files found.)"
        self.rec.log("TOOL EXECUTED: list_files()")
        self.rec.log(block("TOOL RESULT PROVIDED TO MODEL:", result))
        return result

    # -- get_pdf_info ----------------------------------------
    def get_pdf_info(self, filename):
        pages = self.store.pages(filename)
        result = (f"Filename: {filename}\n"
                  f"Size: {self.store.size(filename)} bytes\n"
                  f"Pages: {len(pages)}")
        self.rec.log(f'TOOL EXECUTED: get_pdf_info(filename="{filename}")')
        self.rec.log(block("TOOL RESULT PROVIDED TO MODEL:", result))
        return result

    # -- read_pdf_pages --------------------------------------
    def read_pdf_pages(self, filename, start_page, end_page):
        if start_page < 1:
            return "ERROR: start_page must be at least 1."
        if end_page < start_page:
            return "ERROR: end_page must be greater than or equal to start_page."
        if end_page - start_page + 1 > MAX_PAGES_PER_READ:
            return f"ERROR: You may read at most {MAX_PAGES_PER_READ} pages per request."
        pages = self.store.pages(filename)
        if start_page > len(pages):
            return f"ERROR: PDF contains only {len(pages)} pages."
        actual_end = min(end_page, len(pages))
        out = []
        for p in range(start_page, actual_end + 1):
            out.append(f"========== PAGE {p} ==========")
            lines = pages[p - 1]
            if not lines:
                out.append("[No extractable text found on this page]")
            for n, text in enumerate(lines, start=1):
                out.append(f"[P{p}-L{n}] {text}")
            out.append("")
        result = "\n".join(out)
        self.rec.log(f'TOOL EXECUTED: read_pdf_pages(filename="{filename}", '
                     f'start_page={start_page}, end_page={end_page})')
        self.rec.log(block(
            f'PDF CONTENT PROVIDED TO MODEL FROM "{filename}" '
            f'PAGES {start_page}-{actual_end}:', result, "PDF CONTENT"))
        return result

    # -- search_pdf ------------------------------------------
    def search_pdf(self, filename, query):
        if not query.strip():
            return "ERROR: Search query cannot be empty."
        q = query.lower()
        matches = []
        for p, lines in enumerate(self.store.pages(filename), start=1):
            for n, text in enumerate(lines, start=1):
                if q in text.lower():
                    matches.append(f"[P{p}-L{n}] {text}")
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
            if len(matches) >= MAX_SEARCH_RESULTS:
                break
        if matches:
            result = "\n".join(matches)
            if len(matches) >= MAX_SEARCH_RESULTS:
                result += f"\n\n[Search stopped after {MAX_SEARCH_RESULTS} matches]"
        else:
            result = f'No matches found for "{query}" in {filename}.'
        self.rec.log(f'TOOL EXECUTED: search_pdf(filename="{filename}", query="{query}")')
        self.rec.log(block(f'PDF SEARCH RESULTS PROVIDED TO MODEL FROM "{filename}":',
                           result, "SEARCH RESULT"))
        return result

    # -- select_evidence -------------------------------------
    def select_evidence(self, filename, page, start_line, end_line, purpose):
        pages = self.store.pages(filename)
        if page < 1 or page > len(pages):
            return f"ERROR: PDF contains {len(pages)} pages. Page {page} does not exist."
        if start_line < 1:
            return "ERROR: start_line must be at least 1."
        if end_line < start_line:
            return "ERROR: end_line must be greater than or equal to start_line."
        lines = pages[page - 1]
        if start_line > len(lines):
            return f"ERROR: Page {page} contains only {len(lines)} extracted-text lines."
        actual_end = min(end_line, len(lines))
        text = "\n".join(f"[P{page}-L{n}] {lines[n - 1]}"
                         for n in range(start_line, actual_end + 1))
        self.selected_evidence.append({
            "filename": filename, "page": page, "start_line": start_line,
            "end_line": actual_end, "purpose": purpose, "text": text,
        })
        self.rec.log(
            "MODEL SELECTED EVIDENCE:\n"
            f"FILE: {filename}\nPAGE: {page}\nLINES: {start_line}-{actual_end}\n"
            f"PURPOSE: {purpose}\n"
            f"----- BEGIN SELECTED EVIDENCE -----\n{text}\n"
            "----- END SELECTED EVIDENCE -----")
        return (f"Evidence recorded successfully.\nSource: {filename}, page {page}, "
                f"lines {start_line}-{actual_end}\nPurpose: {purpose}\n\n{text}")

    def execute(self, name, arguments):
        fn = {
            "list_files": self.list_files,
            "get_pdf_info": self.get_pdf_info,
            "read_pdf_pages": self.read_pdf_pages,
            "search_pdf": self.search_pdf,
            "select_evidence": self.select_evidence,
        }.get(name)
        if fn is None:
            return f"ERROR: Unknown tool: {name}"
        try:
            return fn(**arguments)
        except Exception as e:  # report tool errors to the model, keep going
            result = f"ERROR: {e}"
            self.rec.log(f"TOOL ERROR: {name}({json.dumps(arguments)}) -> {result}")
            return result


def _fn(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": required or [], "additionalProperties": False}}}


_FILE = {"type": "string", "description": "Name of the PDF file."}

TOOL_SPECS = [
    _fn("list_files", "List all PDF files currently available for investigation. "
        "Use this first when you do not know which files exist."),
    _fn("get_pdf_info", "Get information about a PDF, including its file size "
        "and number of pages.", {"filename": _FILE}, ["filename"]),
    _fn("read_pdf_pages", "Extract and read text from one or more specific pages "
        "of a PDF. Page numbers start at 1. Returned text contains page and "
        "extracted-text line references.",
        {"filename": _FILE,
         "start_page": {"type": "integer", "description": "First page to read."},
         "end_page": {"type": "integer", "description": "Last page to read."}},
        ["filename", "start_page", "end_page"]),
    _fn("search_pdf", "Search the extracted text of an entire PDF for a word or "
        "phrase. Returns matching lines with page and line references.",
        {"filename": _FILE,
         "query": {"type": "string", "description": "Word or phrase to search for."}},
        ["filename", "query"]),
    _fn("select_evidence", "Explicitly record a passage from a PDF that materially "
        "supports your final answer. Select only evidence that actually "
        "contributes to the answer.",
        {"filename": {"type": "string", "description": "PDF containing the evidence."},
         "page": {"type": "integer", "description": "PDF page containing the evidence."},
         "start_line": {"type": "integer", "description": "First extracted-text line."},
         "end_line": {"type": "integer", "description": "Last extracted-text line."},
         "purpose": {"type": "string",
                     "description": "Which claim or conclusion this evidence supports."}},
        ["filename", "page", "start_line", "end_line", "purpose"]),
]


# ============================================================
# AGENT LOOP
# ============================================================

def make_client():
    from groq import Groq  # imported lazily so jenga.py works without groq
    return Groq()


def run_agent(question, *, files_dir=FILES_DIR, hidden_lines=None, plants=None,
              log_file=LOG_FILE, runs_dir=RUNS_DIR, write_files=True,
              client=None, model=MODEL, temperature=None, label=None, run_id=None):
    """
    Run one agent episode.  Returns a dict with the answer, the selected
    evidence, the run id and the complete log text of this run (which is
    what jenga.py consumes).
    """
    client = client or make_client()
    store = DocumentStore(files_dir, hidden_lines, plants)
    rec = Recorder(log_file, runs_dir, run_id=run_id, write_files=write_files)
    tools = Tools(store, rec)

    rec.log("")
    rec.log("=" * 80)
    rec.log(f"NEW QUESTION: {question}")
    rec.log(f"MODEL: {model}")
    rec.log(f"RUN ID: {rec.run_id}")
    if label:
        rec.log(f"RUN LABEL: {label}")
    if plants:
        rec.log(f"REPLAY PLANTS: {json.dumps(plants, ensure_ascii=False)}")
    if hidden_lines:
        rec.log("REPLAY HIDDEN LINES: " + json.dumps(
            {f: {str(p): sorted(ls) for p, ls in pg.items()}
             for f, pg in hidden_lines.items()}))

    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question}]
    kwargs = {"temperature": temperature} if temperature is not None else {}

    for iteration in range(MAX_ITERATIONS):
        rec.log("")
        rec.log(f"MODEL REQUEST #{iteration + 1}")
        response = client.chat.completions.create(
            model=model, messages=messages, tools=TOOL_SPECS,
            tool_choice="auto", **kwargs)
        message = response.choices[0].message
        content = message.content or ""
        reasoning = getattr(message, "reasoning", None) or ""

        # The agent's own text, logged before anything it triggers.
        if reasoning.strip():
            rec.log(block("MODEL REASONING:", reasoning.strip(), "MODEL TEXT"))
        if content.strip() and message.tool_calls:
            rec.log(block("MODEL TEXT BEFORE TOOL CALL:", content.strip(), "MODEL TEXT"))

        if message.tool_calls:
            messages.append({
                "role": "assistant", "content": content,
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name,
                                             "arguments": tc.function.arguments}}
                               for tc in message.tool_calls]})
            for tc in message.tool_calls:
                name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    result = "ERROR: Model supplied invalid JSON for tool arguments."
                    rec.log(f"INVALID TOOL ARGUMENTS: {tc.function.arguments}")
                else:
                    rec.log(f"MODEL REQUESTED TOOL: {name}("
                            f"{json.dumps(arguments, ensure_ascii=False)})")
                    result = tools.execute(name, arguments)
                rec.step({"tool": name, "arguments": tc.function.arguments,
                          "returned_text": result,
                          "agent_text_before": (reasoning + "\n" + content).strip()})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "name": name, "content": result})
            continue

        # ---- finished ------------------------------------------------
        answer = content
        rec.step({"tool": None, "arguments": None, "returned_text": None,
                  "agent_text_before": (reasoning + "\n" + content).strip()})
        rec.log("")
        rec.log("EVIDENCE SUMMARY")
        if tools.selected_evidence:
            for n, ev in enumerate(tools.selected_evidence, start=1):
                rec.log(f"EVIDENCE #{n}\nFile: {ev['filename']}\nPage: {ev['page']}\n"
                        f"Lines: {ev['start_line']}-{ev['end_line']}\n"
                        f"Purpose: {ev['purpose']}\n{ev['text']}")
        else:
            rec.log("(Model selected no evidence.)")
        rec.log("")
        rec.log(block("FINAL ANSWER:", answer, "FINAL ANSWER"))
        rec.log("=" * 80)
        rec.answer({"question": question, "answer": answer,
                    "selected_evidence": tools.selected_evidence,
                    "plants": plants or {}, "label": label})
        return {"run_id": rec.run_id, "question": question, "answer": answer,
                "selected_evidence": tools.selected_evidence, "log_text": rec.text}

    raise RuntimeError("Agent exceeded the maximum number of investigation rounds.")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("\nGroq PDF Evidence Agent (Jenga-instrumented)\n" + "-" * 44)
    print(f"Model: {MODEL}\nPDF directory: {FILES_DIR}\nAudit log: {LOG_FILE}\n")
    q = input("Question: ")
    print("\nInvestigating PDFs...\n")
    out = run_agent(q)
    print("AI ANSWER\n---------\n" + out["answer"])
    print("\nEXPLICITLY SELECTED EVIDENCE\n----------------------------")
    for n, ev in enumerate(out["selected_evidence"], start=1):
        print(f"\n{n}. {ev['filename']} page {ev['page']}, lines "
              f"{ev['start_line']}-{ev['end_line']}\n   Purpose: {ev['purpose']}\n")
        for line in ev["text"].splitlines():
            print(f"   {line}")
    if not out["selected_evidence"]:
        print("(No evidence was explicitly selected.)")
    print(f"\nFull audit log: {LOG_FILE}\nTrace: {RUNS_DIR / out['run_id']}")
    print("Analyse with:  python jenga.py agent4.log")
