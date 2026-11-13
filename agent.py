import json
from datetime import datetime
from pathlib import Path
from groq import Groq

MODEL = "openai/gpt-oss-120b"

BASE_DIR = Path(__file__).parent
FILES_DIR = BASE_DIR / "ai_files"
LOG_FILE = BASE_DIR / "agent.log"

client = Groq()

def log(message):
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def read_file(filename):
    """
    Read one text file from the files directory.
    """

    # Prevent the AI from escaping our files directory.
    requested_path = (FILES_DIR / filename).resolve()
    allowed_directory = FILES_DIR.resolve()

    if allowed_directory not in requested_path.parents:
        result = "ERROR: Access outside the files directory is not allowed."
        log(f'TOOL ERROR read_file("{filename}") -> {result}')
        return result

    if not requested_path.is_file():
        result = f"ERROR: File does not exist: {filename}"
        log(f'TOOL ERROR read_file("{filename}") -> {result}')
        return result

    try:
        content = requested_path.read_text(encoding="utf-8")
    except Exception as e:
        result = f"ERROR reading file: {e}"
        log(f'TOOL ERROR read_file("{filename}") -> {result}')
        return result

    log(f'TOOL CALL: read_file("{filename}")')

    log(
        f'TOOL RESULT from "{filename}":\n'
        f'----- BEGIN FILE CONTENT -----\n'
        f'{content}\n'
        f'----- END FILE CONTENT -----'
    )

    return content

tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the complete contents of a text file. "
                "Files available for this test are apples.txt "
                "and bananas.txt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to read."
                    }
                },
                "required": ["filename"]
            }
        }
    }
]

def run_agent(question):

    log("")
    log("=" * 70)
    log(f"NEW QUESTION: {question}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a file research assistant. "
                "Use the read_file tool whenever you need information "
                "from one of the available files. "
                "Do not invent file contents."
            )
        },
        {
            "role": "user",
            "content": question
        }
    ]

    for iteration in range(10):

        log(f"MODEL REQUEST #{iteration + 1}")

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        message = response.choices[0].message

        # Did the model request any tools?
        if message.tool_calls:

            messages.append(message)

            for tool_call in message.tool_calls:

                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)

                log(
                    f"MODEL REQUESTED TOOL: "
                    f"{tool_name}({json.dumps(arguments)})"
                )

                if tool_name == "read_file":
                    result = read_file(**arguments)
                else:
                    result = f"ERROR: Unknown tool: {tool_name}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": result
                    }
                )

            # Go around the loop again.
            # The model can inspect another file if it wants.
            continue

        # No tool call means the model has finished.
        answer = message.content or ""

        log(f"FINAL ANSWER:\n{answer}")
        log("=" * 70)

        return answer

    raise RuntimeError("Agent exceeded maximum number of iterations.")


# --------------------------------------------------
# Start
# --------------------------------------------------

if __name__ == "__main__":

    question = input("Question for the AI: ")

    print("\nRunning agent...\n")

    answer = run_agent(question)

    print("AI ANSWER:")
    print(answer)

    print("\nDetailed log written to:")
    print(LOG_FILE)
    