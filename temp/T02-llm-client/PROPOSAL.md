# T02: llm.make_client/default_model with AGENT_BASE_URL/AGENT_MODEL; agent4 uses it

Built against T01 (already on `Vincent`).

## What
- `llm.py`: `default_model()` returns `AGENT_MODEL` or `openai/gpt-oss-120b`. `make_client()` returns a Groq
  client by default, or an OpenAI-compatible client (`openai.OpenAI(base_url=AGENT_BASE_URL, api_key=AGENT_API_KEY or "none")`)
  when `AGENT_BASE_URL` is set. Both imports are lazy. If `openai` is missing you now get a clear ImportError
  telling you to `pip install openai`.
- `agent4.py`: dropped its own `make_client`/hard-coded `MODEL`. Now `MODEL = llm.default_model()` and
  `make_client = llm.make_client`. Both names are kept because app.py uses `agent4.MODEL` and `agent4.make_client`.
  `run_agent(model=None)` falls back to `MODEL`. The CLI banner shows the endpoint.

## Why
PLAN §4/§8: pluggable client so Ollama/II-Search-4B (T11) and the `gpt-oss-20b` fallback need only env vars.

## Notes
- `openai` is not in requirements.txt (outside T02 scope). It's only needed when AGENT_BASE_URL is set.
- The request call in agent4 already uses plain `chat.completions.create` with no Groq-only kwargs, so it works with OpenAI-compatible endpoints.

## Test output
```
python test_jenga.py          -> all ok
python test_replay.py         -> ok test_perturb_changes_value, ok test_replay_claim_stub_shape
python eval/test_eval.py      -> ok test_dry_run
python tools/promote.py --self-test -> ok promote self-test

# AGENT_BASE_URL/AGENT_MODEL unset (GROQ_API_KEY=dummy, construct only):
openai/gpt-oss-120b groq.Groq
# AGENT_MODEL=openai/gpt-oss-20b:
openai/gpt-oss-20b
# AGENT_BASE_URL set, openai not installed:
ImportError: AGENT_BASE_URL is set, so the 'openai' package is needed: pip install openai
```

## Open
- I didn't do a live Groq run: there was no GROQ_API_KEY in the author's environment. The reviewer should run
  `python agent4.py` with a key, with AGENT_BASE_URL unset, and check that the log shows `MODEL: openai/gpt-oss-120b`.
