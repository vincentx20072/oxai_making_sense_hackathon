import json
from datetime import datetime
from pathlib import Path

from groq import Groq
from pypdf import PdfReader


# ============================================================
# CONFIGURATION
# ============================================================

MODEL = "openai/gpt-oss-120b"

BASE_DIR = Path(__file__).parent
FILES_DIR = BASE_DIR / "ai_files"
LOG_FILE = BASE_DIR / "agent4.log"

# Maximum number of PDF pages the model can request at once.
MAX_PAGES_PER_READ = 5

# Maximum number of search matches returned at once.
MAX_SEARCH_RESULTS = 30

client = Groq()

# Evidence explicitly selected by the model during this run.
selected_evidence = []


# ============================================================
# LOGGING
# ============================================================

def log(message):
    """
    Write a timestamped entry to agent4.log.
    """

    timestamp = datetime.now().isoformat(timespec="milliseconds")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


# ============================================================
# SECURITY / PATH HANDLING
# ============================================================

def get_safe_pdf_path(filename):
    """
    Return a safe PDF path inside ai_files.

    Prevents the model from accessing files outside ai_files.
    """

    requested_path = (FILES_DIR / filename).resolve()
    allowed_directory = FILES_DIR.resolve()

    if allowed_directory not in requested_path.parents:
        raise ValueError("Access outside ai_files is not allowed.")

    if requested_path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported.")

    return requested_path


# ============================================================
# PDF TEXT EXTRACTION
# ============================================================

def extract_page_lines(page):
    """
    Extract text from one PDF page and split it into lines.

    These are lines in the extracted text, not necessarily
    physical/visual lines on the rendered PDF page.
    """

    text = page.extract_text() or ""

    return text.splitlines()


# ============================================================
# TOOL 1: LIST FILES
# ============================================================

def list_files():
    """
    Return all PDF files available to the model.
    """

    try:
        files = sorted(
            path.name
            for path in FILES_DIR.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        )

        if files:
            result = "\n".join(files)
        else:
            result = "(No PDF files found.)"

        log("TOOL EXECUTED: list_files()")

        log(
            "TOOL RESULT PROVIDED TO MODEL:\n"
            "----- BEGIN RESULT -----\n"
            f"{result}\n"
            "----- END RESULT -----"
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"

        log(
            f"TOOL ERROR: list_files() -> {result}"
        )

        return result


# ============================================================
# TOOL 2: GET PDF INFO
# ============================================================

def get_pdf_info(filename):
    """
    Return basic information about a PDF.
    """

    try:
        path = get_safe_pdf_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"

            log(
                f'PDF INFO ERROR: "{filename}" -> {result}'
            )

            return result

        reader = PdfReader(path)

        result = (
            f"Filename: {path.name}\n"
            f"Size: {path.stat().st_size} bytes\n"
            f"Pages: {len(reader.pages)}"
        )

        log(
            f'TOOL EXECUTED: get_pdf_info('
            f'filename="{filename}")'
        )

        log(
            "TOOL RESULT PROVIDED TO MODEL:\n"
            "----- BEGIN RESULT -----\n"
            f"{result}\n"
            "----- END RESULT -----"
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"

        log(
            f'TOOL ERROR: get_pdf_info('
            f'"{filename}") -> {result}'
        )

        return result


# ============================================================
# TOOL 3: READ PDF PAGES
# ============================================================

def read_pdf_pages(filename, start_page, end_page):
    """
    Extract text from a specific range of PDF pages.

    Page numbers are 1-based.

    Every extracted line is numbered so we can later identify
    exactly what textual content was supplied to the model.
    """

    try:
        path = get_safe_pdf_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"

            log(
                f'PDF READ ERROR: "{filename}" -> {result}'
            )

            return result

        if start_page < 1:
            return "ERROR: start_page must be at least 1."

        if end_page < start_page:
            return (
                "ERROR: end_page must be greater than or "
                "equal to start_page."
            )

        requested_pages = end_page - start_page + 1

        if requested_pages > MAX_PAGES_PER_READ:
            return (
                f"ERROR: You may read at most "
                f"{MAX_PAGES_PER_READ} pages per request."
            )

        reader = PdfReader(path)
        total_pages = len(reader.pages)

        if start_page > total_pages:
            return (
                f"ERROR: PDF contains only "
                f"{total_pages} pages."
            )

        actual_end_page = min(
            end_page,
            total_pages
        )

        output = []

        for page_number in range(
            start_page,
            actual_end_page + 1
        ):
            page = reader.pages[page_number - 1]

            lines = extract_page_lines(page)

            output.append(
                f"========== PAGE {page_number} =========="
            )

            if not lines:
                output.append(
                    "[No extractable text found on this page]"
                )

            else:
                for line_number, text in enumerate(
                    lines,
                    start=1
                ):
                    output.append(
                        f"[P{page_number}-L{line_number}] {text}"
                    )

            output.append("")

        result = "\n".join(output)

        log(
            f'TOOL EXECUTED: read_pdf_pages('
            f'filename="{filename}", '
            f'start_page={start_page}, '
            f'end_page={end_page})'
        )

        log(
            f'PDF CONTENT PROVIDED TO MODEL '
            f'FROM "{filename}" '
            f'PAGES {start_page}-{actual_end_page}:\n'
            f'----- BEGIN PDF CONTENT -----\n'
            f'{result}\n'
            f'----- END PDF CONTENT -----'
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"

        log(
            f'TOOL ERROR: read_pdf_pages('
            f'"{filename}", '
            f'{start_page}, '
            f'{end_page}) -> {result}'
        )

        return result


# ============================================================
# TOOL 4: SEARCH PDF
# ============================================================

def search_pdf(filename, query):
    """
    Search all extracted text in a PDF.

    Search is case-insensitive.

    The matching text itself is returned to the model, meaning
    search results count as information that the model has seen.
    """

    try:
        path = get_safe_pdf_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"

            log(
                f'PDF SEARCH ERROR: "{filename}" -> {result}'
            )

            return result

        if not query.strip():
            return "ERROR: Search query cannot be empty."

        reader = PdfReader(path)

        query_lower = query.lower()

        matches = []

        for page_number, page in enumerate(
            reader.pages,
            start=1
        ):
            lines = extract_page_lines(page)

            for line_number, text in enumerate(
                lines,
                start=1
            ):
                if query_lower in text.lower():

                    matches.append(
                        f"[P{page_number}-L{line_number}] "
                        f"{text}"
                    )

                    if len(matches) >= MAX_SEARCH_RESULTS:
                        break

            if len(matches) >= MAX_SEARCH_RESULTS:
                break

        if matches:
            result = "\n".join(matches)

            if len(matches) >= MAX_SEARCH_RESULTS:
                result += (
                    f"\n\n[Search stopped after "
                    f"{MAX_SEARCH_RESULTS} matches]"
                )

        else:
            result = (
                f'No matches found for "{query}" '
                f'in {filename}.'
            )

        log(
            f'TOOL EXECUTED: search_pdf('
            f'filename="{filename}", '
            f'query="{query}")'
        )

        log(
            f'PDF SEARCH RESULTS PROVIDED TO MODEL '
            f'FROM "{filename}":\n'
            f'----- BEGIN SEARCH RESULT -----\n'
            f'{result}\n'
            f'----- END SEARCH RESULT -----'
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"

        log(
            f'TOOL ERROR: search_pdf('
            f'"{filename}", "{query}") -> {result}'
        )

        return result


# ============================================================
# TOOL 5: SELECT EVIDENCE
# ============================================================

def select_evidence(
    filename,
    page,
    start_line,
    end_line,
    purpose
):
    """
    Allow the model to explicitly identify PDF text that
    materially supports its final answer.

    This does NOT reveal the model's internal attention or
    hidden reasoning.

    It records evidence that the model explicitly identifies
    as supporting its answer.
    """

    try:
        path = get_safe_pdf_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"

            log(
                f'EVIDENCE ERROR: "{filename}" -> {result}'
            )

            return result

        reader = PdfReader(path)

        total_pages = len(reader.pages)

        if page < 1 or page > total_pages:
            return (
                f"ERROR: PDF contains {total_pages} pages. "
                f"Page {page} does not exist."
            )

        if start_line < 1:
            return "ERROR: start_line must be at least 1."

        if end_line < start_line:
            return (
                "ERROR: end_line must be greater than or "
                "equal to start_line."
            )

        pdf_page = reader.pages[page - 1]

        lines = extract_page_lines(pdf_page)

        if start_line > len(lines):
            return (
                f"ERROR: Page {page} contains only "
                f"{len(lines)} extracted-text lines."
            )

        actual_end = min(
            end_line,
            len(lines)
        )

        chosen_lines = lines[
            start_line - 1:actual_end
        ]

        numbered_lines = []

        for line_number, text in enumerate(
            chosen_lines,
            start=start_line
        ):
            numbered_lines.append(
                f"[P{page}-L{line_number}] {text}"
            )

        evidence_text = "\n".join(
            numbered_lines
        )

        evidence_record = {
            "filename": filename,
            "page": page,
            "start_line": start_line,
            "end_line": actual_end,
            "purpose": purpose,
            "text": evidence_text
        }

        selected_evidence.append(
            evidence_record
        )

        result = (
            f"Evidence recorded successfully.\n"
            f"Source: {filename}, "
            f"page {page}, "
            f"lines {start_line}-{actual_end}\n"
            f"Purpose: {purpose}\n\n"
            f"{evidence_text}"
        )

        log(
            "MODEL SELECTED EVIDENCE:\n"
            f"FILE: {filename}\n"
            f"PAGE: {page}\n"
            f"LINES: {start_line}-{actual_end}\n"
            f"PURPOSE: {purpose}\n"
            "----- BEGIN SELECTED EVIDENCE -----\n"
            f"{evidence_text}\n"
            "----- END SELECTED EVIDENCE -----"
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"

        log(
            f'TOOL ERROR: select_evidence('
            f'"{filename}", '
            f'page={page}, '
            f'lines={start_line}-{end_line}) '
            f'-> {result}'
        )

        return result


# ============================================================
# TOOL DEFINITIONS SHOWN TO THE MODEL
# ============================================================

tools = [

    # --------------------------------------------------------
    # list_files
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List all PDF files currently available for "
                "investigation. Use this first when you do not "
                "know which files exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # get_pdf_info
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "get_pdf_info",
            "description": (
                "Get information about a PDF, including its "
                "file size and number of pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {

                    "filename": {
                        "type": "string",
                        "description": "Name of the PDF file."
                    }
                },
                "required": [
                    "filename"
                ],
                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # read_pdf_pages
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "read_pdf_pages",
            "description": (
                "Extract and read text from one or more specific "
                "pages of a PDF. Page numbers start at 1. "
                "Returned text contains page and extracted-text "
                "line references. Use this when you need the "
                "actual contents and surrounding context."
            ),
            "parameters": {
                "type": "object",
                "properties": {

                    "filename": {
                        "type": "string",
                        "description": "Name of the PDF file."
                    },

                    "start_page": {
                        "type": "integer",
                        "description": "First page to read."
                    },

                    "end_page": {
                        "type": "integer",
                        "description": "Last page to read."
                    }
                },

                "required": [
                    "filename",
                    "start_page",
                    "end_page"
                ],

                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # search_pdf
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "search_pdf",
            "description": (
                "Search the extracted text of an entire PDF "
                "for a word or phrase. Returns matching text "
                "together with page and extracted-text line "
                "references. Use this to locate relevant "
                "sections before reading surrounding pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {

                    "filename": {
                        "type": "string",
                        "description": "Name of the PDF file."
                    },

                    "query": {
                        "type": "string",
                        "description": (
                            "Word or phrase to search for."
                        )
                    }
                },

                "required": [
                    "filename",
                    "query"
                ],

                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # select_evidence
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "select_evidence",
            "description": (
                "Explicitly record a passage from a PDF that "
                "materially supports your final answer. Use this "
                "after locating and reading relevant information "
                "and before giving your final answer. Select only "
                "evidence that actually contributes to the answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {

                    "filename": {
                        "type": "string",
                        "description": (
                            "Name of the PDF containing "
                            "the evidence."
                        )
                    },

                    "page": {
                        "type": "integer",
                        "description": (
                            "PDF page containing the evidence."
                        )
                    },

                    "start_line": {
                        "type": "integer",
                        "description": (
                            "First extracted-text line "
                            "of the evidence."
                        )
                    },

                    "end_line": {
                        "type": "integer",
                        "description": (
                            "Last extracted-text line "
                            "of the evidence."
                        )
                    },

                    "purpose": {
                        "type": "string",
                        "description": (
                            "Briefly state which claim or "
                            "conclusion this evidence supports."
                        )
                    }
                },

                "required": [
                    "filename",
                    "page",
                    "start_line",
                    "end_line",
                    "purpose"
                ],

                "additionalProperties": False
            }
        }
    }
]


# ============================================================
# EXECUTE TOOL
# ============================================================

def execute_tool(tool_name, arguments):
    """
    Dispatch a model tool request to the correct Python function.
    """

    if tool_name == "list_files":
        return list_files()

    elif tool_name == "get_pdf_info":
        return get_pdf_info(**arguments)

    elif tool_name == "read_pdf_pages":
        return read_pdf_pages(**arguments)

    elif tool_name == "search_pdf":
        return search_pdf(**arguments)

    elif tool_name == "select_evidence":
        return select_evidence(**arguments)

    else:
        return f"ERROR: Unknown tool: {tool_name}"


# ============================================================
# AGENT LOOP
# ============================================================

def run_agent(question):

    # Start each question with a clean evidence list.
    selected_evidence.clear()

    log("")
    log("=" * 80)
    log(f"NEW QUESTION: {question}")
    log(f"MODEL: {MODEL}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a research assistant with access to "
                "a directory containing PDF documents.\n\n"

                "You do not know which PDFs exist until you use "
                "list_files.\n\n"

                "Use the provided tools to investigate the PDFs "
                "and answer the user's question.\n\n"

                "You can search PDF text with search_pdf and "
                "inspect complete pages with read_pdf_pages.\n\n"

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

                "The purpose field should briefly describe what "
                "claim that evidence supports. It should describe "
                "the evidential relationship, not provide hidden "
                "reasoning or chain-of-thought.\n\n"

                "Do not claim that a PDF contains information "
                "unless that information has actually been "
                "returned to you by one of the tools.\n\n"

                "In your final answer, cite selected evidence "
                "using references such as "
                "[filename, P2-L14:L18].\n\n"

                "Extracted PDF text may not perfectly represent "
                "visual layout, columns, images, charts, or "
                "other graphical content."
            )
        },

        {
            "role": "user",
            "content": question
        }
    ]

    # Allow multiple rounds of PDF investigation.
    for iteration in range(30):

        log("")
        log(f"MODEL REQUEST #{iteration + 1}")

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        message = response.choices[0].message

        # ----------------------------------------------------
        # MODEL REQUESTED TOOL(S)
        # ----------------------------------------------------

        if message.tool_calls:

            messages.append(message)

            for tool_call in message.tool_calls:

                tool_name = tool_call.function.name

                try:
                    arguments = json.loads(
                        tool_call.function.arguments
                    )

                except json.JSONDecodeError:

                    result = (
                        "ERROR: Model supplied invalid JSON "
                        "for tool arguments."
                    )

                    log(
                        "INVALID TOOL ARGUMENTS: "
                        f"{tool_call.function.arguments}"
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": result
                        }
                    )

                    continue

                log(
                    "MODEL REQUESTED TOOL: "
                    f"{tool_name}("
                    f"{json.dumps(arguments, ensure_ascii=False)}"
                    f")"
                )

                result = execute_tool(
                    tool_name,
                    arguments
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": result
                    }
                )

            continue

        # ----------------------------------------------------
        # MODEL FINISHED
        # ----------------------------------------------------

        answer = message.content or ""

        # ----------------------------------------------------
        # EVIDENCE SUMMARY
        # ----------------------------------------------------

        log("")
        log("EVIDENCE SUMMARY")

        if selected_evidence:

            for number, evidence in enumerate(
                selected_evidence,
                start=1
            ):
                log(
                    f"EVIDENCE #{number}\n"
                    f"File: {evidence['filename']}\n"
                    f"Page: {evidence['page']}\n"
                    f"Lines: "
                    f"{evidence['start_line']}-"
                    f"{evidence['end_line']}\n"
                    f"Purpose: {evidence['purpose']}\n"
                    f"{evidence['text']}"
                )

        else:
            log("(Model selected no evidence.)")

        # ----------------------------------------------------
        # FINAL ANSWER
        # ----------------------------------------------------

        log("")
        log(
            "FINAL ANSWER:\n"
            "----- BEGIN FINAL ANSWER -----\n"
            f"{answer}\n"
            "----- END FINAL ANSWER -----"
        )

        log("=" * 80)

        return answer

    raise RuntimeError(
        "Agent exceeded the maximum number of investigation rounds."
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("Groq PDF Evidence Agent")
    print("-----------------------")
    print(f"Model: {MODEL}")
    print(f"PDF directory: {FILES_DIR}")
    print(f"Audit log: {LOG_FILE}")
    print()

    question = input("Question: ")

    print()
    print("Investigating PDFs...")
    print()

    answer = run_agent(question)

    print("AI ANSWER")
    print("---------")
    print(answer)

    print()
    print("EXPLICITLY SELECTED EVIDENCE")
    print("----------------------------")

    if selected_evidence:

        for number, evidence in enumerate(
            selected_evidence,
            start=1
        ):
            print()
            print(
                f"{number}. "
                f"{evidence['filename']} "
                f"page {evidence['page']}, "
                f"lines "
                f"{evidence['start_line']}-"
                f"{evidence['end_line']}"
            )

            print(
                f"   Purpose: {evidence['purpose']}"
            )

            print()

            for line in evidence["text"].splitlines():
                print(f"   {line}")

    else:
        print("(No evidence was explicitly selected.)")

    print()
    print(f"Full audit log: {LOG_FILE}")