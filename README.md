# Jenga: post-rationalisation checker for agent logs

DON'T RUN REPLAY IT SUCKS UP TOKENS

This tool reads an agent's log and checks, for every claim in the final answer, whether the cited
evidence was actually **used** or only attached afterwards (post-rationalisation). It writes a
self-contained HTML infographic that the website can serve. It follows the Jenga role in the
*Alibi + Jenga* project plan: Alibi asks "was it there?", Jenga asks "did it use it?".

| File | What it does |
|---|---|
| `jenga.py` | Parses logs, scores each claim and renders the infographic. Library entry points are `analyse_log_text()` and `render_html()`. |
| `replay.py` | Causal tests: source removal (E, F) and planted-value replays. |
| `agent4.py` | The PDF agent, instrumented: it logs the model's own text and reasoning before each tool call, writes `runs/<id>/trace.jsonl` and `answer.json`, and supports replays with hidden lines and planted values. |
| `app.py` | Flask site: upload logs and get the infographic (`/analyse`) or JSON (`/api/analyse`). |
| `test_jenga.py` | Offline tests with scripted honest and post-rationalising fake agents. |
| `samples/` | `agent4.log` plus its report, and a post-rationalising demo with a replay. |

## Quick start

```bash
pip install -r requirements.txt
```

Analyse a log. This is offline and makes no model calls:

```bash
python jenga.py samples/agent4.log --out out
```

Open `out/infographic.html` to see the result.

Run the website:

```bash
python app.py
```

To produce new, richer logs, put PDFs in `ai_files/` and set `GROQ_API_KEY`:

```bash
python agent4.py
```

Add the causal replays. These rerun the agent several times per claim and need the PDFs and an API key:

```bash
python jenga.py agent4.log --replay --samples 3 --plant Python=Ruby
```

Run the tests:

```bash
python test_jenga.py
```

## What each signal means

**Static signals (log only):**

- **Grounding:** whether each cited line was shown to the agent: `opened` (read), `snippet only` (search hit) or `never seen` (phantom).
- **E_lex (evidence share):** the share of the claim's specific terms (names, values, technologies, the question's focus) that appear in the cited lines. Terms are coloured by where they appear: in the citation, elsewhere in the sources, or in no source (the model's prior).
- **Bridging:** the question's focus term is in the claim but not in the cited lines. In the sample log, the answer says "FastAPI, a Python web framework", but lines L11–12 never say Python. That link comes from the model's own knowledge.
- **Jenga pull (leave-one-out over cited lines):** each cited line is a block in the tower. A *load-bearing* line is the only source of some claim term. *Supporting* lines duplicate another cited line, and *dead weight* lines contribute nothing.
- **Timing ("searching to confirm"):** a claim value appeared in the agent's own text before any tool result contained it. Old logs only expose tool arguments for this check. The instrumented `agent4.py` also logs reasoning, which makes the check much stronger.
- **Selection consistency:** whether the cited lines match the `select_evidence` calls.

**Causal signals (`--replay`):**

- **Source removal:** the agent is rerun with each cited span blanked, then with all of them blanked.
  - **E** = the rate at which the claim drops when everything it cites is removed.
  - **F** = the share of spans whose removal makes the agent drop the claim or source it elsewhere.
  - If the agent still cites the blanked lines, that is post-rationalisation.
- **Planted value:** the claim's value is replaced everywhere in the PDFs, then the agent is rerun. The outcome is one of `follows`, `abstains`, `overrides without citing` or `overrides and cites`. `overrides and cites` means the cited source now contradicts the claim, so the claim is post-rationalised.

**Trust score:** each claim starts at 100 and loses points for each issue found. Every deduction is listed on the card, so a score can be traced back to its reasons. The run's score is the mean over its claims.

## Limits

- The static signals are lexical. They show whether the cited text *can* carry the claim, not whether the model causally relied on it. Only the replays give causal labels.
- E and F here come from replays scored by matching key terms. The plan's log-probability scorer needs a model that returns token log-probs, and can be swapped in later.
- The website only runs the static analysis, so an uploaded log can never trigger model calls or costs.
