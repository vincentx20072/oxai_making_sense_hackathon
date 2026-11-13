import json
from datetime import datetime
from pathlib import Path

from groq import Groq


# ============================================================
# CONFIGURATION
# ============================================================

MODEL = "openai/gpt-oss-120b"

BASE_DIR = Path(__file__).parent
FILES_DIR = BASE_DIR / "ai_files"
LOG_FILE = BASE_DIR / "agent2.log"

MAX_LINES_PER_READ = 200
MAX_SEARCH_RESULTS = 20

client = Groq()


# ============================================================
# LOGGING
# ============================================================

def log(message):
    """
    Write a timestamped entry to agent2.log.
    """

    timestamp = datetime.now().isoformat(timespec="milliseconds")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


# ============================================================
# SECURITY / PATH HANDLING
# ============================================================

def get_safe_path(filename):
    """
    Convert a filename into a safe path inside ai_files.

    This prevents the model from doing things such as:
        ../secret.txt
        C:\\somewhere\\secret.txt
    """

    requested_path = (FILES_DIR / filename).resolve()
    allowed_directory = FILES_DIR.resolve()

    if allowed_directory not in requested_path.parents:
        raise ValueError("Access outside ai_files is not allowed.")

    return requested_path


# ============================================================
# TOOL 1: LIST FILES
# ============================================================

def list_files():
    """
    Return all .txt files available to the model.
    """

    files = sorted(
        path.name
        for path in FILES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() == ".txt"
    )

    result = "\n".join(files)

    if not result:
        result = "(No .txt files found.)"

    log("TOOL EXECUTED: list_files()")
    log(
        "TOOL RESULT:\n"
        "----- BEGIN RESULT -----\n"
        f"{result}\n"
        "----- END RESULT -----"
    )

    return result


# ============================================================
# TOOL 2: GET FILE INFO
# ============================================================

def get_file_info(filename):
    """
    Return basic information about a text file.
    """

    try:
        path = get_safe_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"
            log(f'TOOL ERROR: get_file_info("{filename}") -> {result}')
            return result

        if path.suffix.lower() != ".txt":
            result = "ERROR: Only .txt files are supported."
            log(f'TOOL ERROR: get_file_info("{filename}") -> {result}')
            return result

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        result = (
            f"Filename: {path.name}\n"
            f"Size: {path.stat().st_size} bytes\n"
            f"Lines: {len(lines)}"
        )

        log(f'TOOL EXECUTED: get_file_info("{filename}")')
        log(
            "TOOL RESULT:\n"
            "----- BEGIN RESULT -----\n"
            f"{result}\n"
            "----- END RESULT -----"
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"
        log(f'TOOL ERROR: get_file_info("{filename}") -> {result}')
        return result


# ============================================================
# TOOL 3: READ LINES
# ============================================================

def read_lines(filename, start_line, end_line):
    """
    Read a specific range of lines from a text file.

    Line numbers are 1-based.
    """

    try:
        path = get_safe_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"
            log(f'TOOL ERROR: read_lines("{filename}") -> {result}')
            return result

        if path.suffix.lower() != ".txt":
            result = "ERROR: Only .txt files are supported."
            log(f'TOOL ERROR: read_lines("{filename}") -> {result}')
            return result

        lines = path.read_text(encoding="utf-8").splitlines()

        total_lines = len(lines)

        if start_line < 1:
            return "ERROR: start_line must be at least 1."

        if end_line < start_line:
            return "ERROR: end_line must be greater than or equal to start_line."

        requested_count = end_line - start_line + 1

        if requested_count > MAX_LINES_PER_READ:
            return (
                f"ERROR: You may read at most "
                f"{MAX_LINES_PER_READ} lines per request."
            )

        if start_line > total_lines:
            return (
                f"ERROR: File only contains {total_lines} lines."
            )

        # Don't go beyond the end of the file.
        actual_end = min(end_line, total_lines)

        selected_lines = lines[start_line - 1:actual_end]

        numbered_lines = []

        for line_number, text in enumerate(
            selected_lines,
            start=start_line
        ):
            numbered_lines.append(
                f"{line_number:06d} | {text}"
            )

        result = "\n".join(numbered_lines)

        log(
            f'TOOL EXECUTED: read_lines('
            f'filename="{filename}", '
            f'start_line={start_line}, '
            f'end_line={end_line})'
        )

        log(
            f'CONTENT PROVIDED TO MODEL '
            f'FROM "{filename}" '
            f'LINES {start_line}-{actual_end}:\n'
            f'----- BEGIN FILE CONTENT -----\n'
            f'{result}\n'
            f'----- END FILE CONTENT -----'
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"
        log(
            f'TOOL ERROR: read_lines("{filename}", '
            f'{start_line}, {end_line}) -> {result}'
        )
        return result


# ============================================================
# TOOL 4: SEARCH FILE
# ============================================================

def search_file(filename, query):
    """
    Search a text file for lines containing a query.

    Search is case-insensitive.
    """

    try:
        path = get_safe_path(filename)

        if not path.is_file():
            result = f"ERROR: File does not exist: {filename}"
            log(f'TOOL ERROR: search_file("{filename}") -> {result}')
            return result

        if path.suffix.lower() != ".txt":
            result = "ERROR: Only .txt files are supported."
            log(f'TOOL ERROR: search_file("{filename}") -> {result}')
            return result

        lines = path.read_text(encoding="utf-8").splitlines()

        query_lower = query.lower()

        matches = []

        for line_number, text in enumerate(lines, start=1):

            if query_lower in text.lower():
                matches.append(
                    f"{line_number:06d} | {text}"
                )

            if len(matches) >= MAX_SEARCH_RESULTS:
                break

        if matches:
            result = "\n".join(matches)
        else:
            result = f'No matches found for "{query}".'

        log(
            f'TOOL EXECUTED: search_file('
            f'filename="{filename}", '
            f'query="{query}")'
        )

        log(
            "SEARCH RESULT PROVIDED TO MODEL:\n"
            "----- BEGIN SEARCH RESULT -----\n"
            f"{result}\n"
            "----- END SEARCH RESULT -----"
        )

        return result

    except Exception as e:
        result = f"ERROR: {e}"
        log(
            f'TOOL ERROR: search_file('
            f'"{filename}", "{query}") -> {result}'
        )
        return result


# ============================================================
# TOOL DEFINITIONS FOR THE MODEL
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
                "List all text files currently available "
                "for investigation."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # get_file_info
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": (
                "Get information about a text file, including "
                "its size and number of lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the text file."
                    }
                },
                "required": ["filename"],
                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # read_lines
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "read_lines",
            "description": (
                "Read a specific range of lines from a text file. "
                "Use this when you need to inspect the actual "
                "contents of a file. Line numbers start at 1."
            ),
            "parameters": {
                "type": "object",
                "properties": {

                    "filename": {
                        "type": "string",
                        "description": "Name of the text file."
                    },

                    "start_line": {
                        "type": "integer",
                        "description": "First line to read."
                    },

                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read."
                    }
                },
                "required": [
                    "filename",
                    "start_line",
                    "end_line"
                ],
                "additionalProperties": False
            }
        }
    },

    # --------------------------------------------------------
    # search_file
    # --------------------------------------------------------

    {
        "type": "function",
        "function": {
            "name": "search_file",
            "description": (
                "Search a text file for a word or phrase. "
                "Returns matching lines and their line numbers. "
                "Useful for locating relevant parts of large files "
                "before reading the surrounding lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {

                    "filename": {
                        "type": "string",
                        "description": "Name of the text file."
                    },

                    "query": {
                        "type": "string",
                        "description": "Text to search for."
                    }
                },
                "required": [
                    "filename",
                    "query"
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

    elif tool_name == "get_file_info":
        return get_file_info(**arguments)

    elif tool_name == "read_lines":
        return read_lines(**arguments)

    elif tool_name == "search_file":
        return search_file(**arguments)

    else:
        return f"ERROR: Unknown tool: {tool_name}"


# ============================================================
# AGENT LOOP
# ============================================================

def run_agent(question):

    log("")
    log("=" * 80)
    log(f"NEW QUESTION: {question}")
    log(f"MODEL: {MODEL}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a research assistant with access to "
                "a directory of text files.\n\n"

                "You do not know which files exist until you use "
                "list_files.\n\n"

                "Use the provided tools to investigate the files "
                "when necessary.\n\n"

                "For large files, prefer searching for relevant "
                "terms and then reading the surrounding lines "
                "rather than reading the entire file.\n\n"

                "Do not claim that a file contains information "
                "unless that information has actually been returned "
                "to you by one of the tools.\n\n"

                "When answering factual questions about the files, "
                "mention the filename and line numbers that support "
                "your answer when possible."
            )
        },

        {
            "role": "user",
            "content": question
        }
    ]

    # Allow several rounds of investigation.
    for iteration in range(20):

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
        # MODEL REQUESTED TOOLS
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
                        f"INVALID TOOL ARGUMENTS: "
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
                    f"MODEL REQUESTED TOOL: "
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
    print("Groq File Research Agent")
    print("------------------------")
    print(f"Model: {MODEL}")
    print(f"Files directory: {FILES_DIR}")
    print(f"Log file: {LOG_FILE}")
    print()

    question = input("Question: ")

    print()
    print("Investigating...")
    print()

    answer = run_agent(question)

    print("AI ANSWER")
    print("---------")
    print(answer)

    print()
    print(f"Full audit log: {LOG_FILE}")