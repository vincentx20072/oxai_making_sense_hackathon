"""
Model client factory.

    make_client()   Anthropic Claude client by default.  An OpenAI-compatible
                    client is retained when AGENT_BASE_URL is set.
    default_model() AGENT_MODEL, else claude-sonnet-4-5.
"""

import json
import os

DEFAULT_MODEL = "claude-sonnet-4-5"
FALLBACK_MODEL = "claude-haiku-4-5"


def default_model():
    return os.environ.get("AGENT_MODEL") or DEFAULT_MODEL


def make_client():
    base = os.environ.get("AGENT_BASE_URL")
    if base:
        try:
            from openai import OpenAI  # optional dependency, only for AGENT_BASE_URL
        except ImportError as e:
            raise ImportError("AGENT_BASE_URL is set, so the 'openai' package is "
                              "needed: pip install openai") from e
        return OpenAI(base_url=base, api_key=os.environ.get("AGENT_API_KEY") or "none")

    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise ImportError(
            "Claude support requires the 'anthropic' package: pip install anthropic"
        ) from e

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise ValueError(
            "Set ANTHROPIC_API_KEY or CLAUDE_API_KEY to use Claude"
        )
    return _ClaudeChatCompletionsAdapter(
        Anthropic(api_key=api_key),
    )


class _ChoiceMessage:
    def __init__(self, content="", tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning = reasoning


class _Choice:
    def __init__(self, message):
        self.message = message


class _Response:
    def __init__(self, choice_message):
        self.choices = [_Choice(choice_message)]


class _ToolCallFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = _ToolCallFunction(name, arguments)


class _ClaudeCompletions:
    def __init__(self, anthropic_client):
        self._client = anthropic_client

    def create(self, *, model, messages, tools=None, tool_choice=None,
               temperature=None, max_tokens=None, **kwargs):
        system_parts = []
        claude_messages = []

        for message in messages:
            role = message.get("role")
            content = message.get("content") or ""

            if role == "system":
                system_parts.append(content)
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": content,
                }
                _append_claude_message(claude_messages, "user", block)
            elif role == "assistant" and message.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tool_call in message["tool_calls"]:
                    function = tool_call["function"]
                    arguments = function.get("arguments") or "{}"
                    try:
                        parsed_arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        parsed_arguments = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tool_call["id"],
                        "name": function["name"],
                        "input": parsed_arguments,
                    })
                _append_claude_message(claude_messages, "assistant", *blocks)
            else:
                role = "assistant" if role == "assistant" else "user"
                _append_claude_message(claude_messages, role, {
                    "type": "text",
                    "text": content,
                })

        claude_tools = None
        if tools:
            claude_tools = []
            for tool in tools:
                function = tool["function"]
                claude_tools.append({
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters", {"type": "object"}),
                })

        request = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": max_tokens or 4096,
        }
        if system_parts:
            request["system"] = "\n\n".join(system_parts)
        if claude_tools:
            request["tools"] = claude_tools
        if tool_choice == "auto":
            request["tool_choice"] = {"type": "auto"}
        elif tool_choice:
            request["tool_choice"] = tool_choice
        if temperature is not None:
            request["temperature"] = temperature

        response = self._client.messages.create(**request)
        text_parts = []
        reasoning_parts = []
        tool_calls = []

        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "thinking" and getattr(block, "thinking", None):
                reasoning_parts.append(block.thinking)
            elif block_type == "tool_use":
                tool_calls.append(_ToolCall(
                    block.id,
                    block.name,
                    json.dumps(block.input, ensure_ascii=False),
                ))

        message = _ChoiceMessage(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            reasoning="\n".join(reasoning_parts) or None,
        )
        return _Response(message)


class _ClaudeChatCompletionsAdapter:
    def __init__(self, anthropic_client):
        self.completions = _ClaudeCompletions(anthropic_client)
        self.chat = type("Chat", (), {"completions": self.completions})()


def _append_claude_message(messages, role, *blocks):
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": list(blocks)})
