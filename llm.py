"""
Model clients and settings.

    make_client()    Anthropic client by default (Claude).  Groq / an
                     OpenAI-compatible endpoint when AGENT_PROVIDER=groq or
                     AGENT_BASE_URL is set, or when GROQ_API_KEY is the only
                     key (the Vercel demo).  Without a Claude key the Alibi
                     judge reports "unavailable" and makes no call.
    default_model()  AGENT_MODEL, else claude-sonnet-5-5 (Groq: gpt-oss-120b).
    agent_effort()   AGENT_EFFORT, else "medium".
    judge_model()    JUDGE_MODEL, else claude-haiku-5-5 (used by judge.py).
    cost_usd(model, usage)  dollars for a usage dict (see PRICES).

Credentials: ANTHROPIC_API_KEY from the environment, or from a git-ignored
.env file next to this module (KEY=value lines), loaded on import.
"""

import os
from pathlib import Path

ANTHROPIC_MODEL = "claude-sonnet-5-5"   # the agent and its replays (same model, so replays measure it)
ANTHROPIC_JUDGE = "claude-haiku-5-5"
GROQ_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"   # Groq fallback, kept for the old setup

# $ per million tokens: input, output, cache read, cache write (5-minute TTL).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def _load_dotenv(path=Path(__file__).with_name(".env")):
    """Minimal .env reader: KEY=value lines; never overrides the real environment."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _has_anthropic_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def provider():
    if os.environ.get("AGENT_BASE_URL") or os.environ.get("AGENT_PROVIDER", "").lower() == "groq":
        return "openai-compatible"
    if os.environ.get("GROQ_API_KEY") and not _has_anthropic_key():
        return "openai-compatible"   # only a Groq key (e.g. the Vercel demo): use Groq
    return "anthropic"


def default_model():
    if os.environ.get("AGENT_MODEL"):
        return os.environ["AGENT_MODEL"]
    return ANTHROPIC_MODEL if provider() == "anthropic" else GROQ_MODEL


def agent_effort():
    return os.environ.get("AGENT_EFFORT") or "medium"


def judge_model():
    return os.environ.get("JUDGE_MODEL") or ANTHROPIC_JUDGE


def has_credentials():
    if provider() == "anthropic":
        return _has_anthropic_key()
    return bool(os.environ.get("GROQ_API_KEY") or os.environ.get("AGENT_BASE_URL"))


def make_client():
    if provider() == "anthropic":
        import anthropic  # imported lazily so offline tests need no SDK
        return anthropic.Anthropic()
    base = os.environ.get("AGENT_BASE_URL")
    if base:
        try:
            from openai import OpenAI  # optional dependency, only for AGENT_BASE_URL
        except ImportError as e:
            raise ImportError("AGENT_BASE_URL is set, so the 'openai' package is "
                              "needed: pip install openai") from e
        return OpenAI(base_url=base, api_key=os.environ.get("AGENT_API_KEY") or "none")
    from groq import Groq
    return Groq()


def add_usage(total, usage):
    """Accumulate an Anthropic usage object into a plain dict."""
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens"):
        total[k] = total.get(k, 0) + (getattr(usage, k, None) or 0)
    return total


def cost_usd(model, usage):
    """Dollar cost of a usage dict, or None for a model without a price."""
    p = PRICES.get(model)
    if not p or not usage:
        return None
    inp, out, cread, cwrite = p
    return round((usage.get("input_tokens", 0) * inp
                  + usage.get("output_tokens", 0) * out
                  + usage.get("cache_read_input_tokens", 0) * cread
                  + usage.get("cache_creation_input_tokens", 0) * cwrite) / 1e6, 4)
