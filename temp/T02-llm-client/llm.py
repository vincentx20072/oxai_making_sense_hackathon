"""
Model client factory (owned by Dev 2; finished in T02).

    make_client()   Groq client by default; an OpenAI-compatible client when
                    AGENT_BASE_URL is set (e.g. Ollama at http://localhost:11434/v1).
    default_model() AGENT_MODEL, else openai/gpt-oss-120b.
"""

import os

DEFAULT_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"


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
    from groq import Groq  # imported lazily so offline tests need no groq
    return Groq()
