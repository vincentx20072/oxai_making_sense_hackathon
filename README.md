# Jenga: ask about PDFs, then check whether the answer's citations hold up

Jenga is a Flask web app. You upload PDFs and write a prompt, and an AI agent (Claude Opus 5.5)
answers with line-level citations. Two checks then test each claim in the answer:

- **Alibi**, "was it there?": are the claim's facts in the lines the agent was shown? A Claude
  Haiku 5.5 judge splits each claim into atomic facts and finds the line for each one.
- **Jenga**, "did it use it?": did the answer depend on its citations, or was it written first and
  the citation found afterwards (*post-rationalisation*)?

The **answer traceability** score (0–100) rates how well the answer traces back to the PDFs. It is
not a rating of the documents: a weak CV described accurately scores high.

## Run it

```bash
pip install -r requirements.txt
echo ANTHROPIC_API_KEY=sk-ant-... > .env    # git-ignored; read by llm.py
python app.py
```

Then open http://localhost:5000. A typical run costs about $0.08 (agent) plus $0.004 (judge).

Settings (environment variables or `.env`): `AGENT_MODEL` (default `claude-opus-5-5`),
`AGENT_EFFORT` (default `medium`), `JUDGE_MODEL` (default `claude-haiku-5-5`). The old Groq
setup still works with `AGENT_PROVIDER=groq` and `GROQ_API_KEY`.

| Page | What you get |
|---|---|
| **New question** | Drag in PDFs (PDF only, up to 10 files and 25 MB), write a prompt, and run. The page shows the agent's steps live. |
| **Answer** tab | The answer with citation chips (hover to see the lines, click to open the PDF page), and the **Summary report**: every claim placed in the Alibi × Jenga grid. |
| **Alibi** tab | Each claim with the judge's verdict and its atomic facts, each marked found / not found / contradicted with the line it came from. |
| **Jenga** tab | The traceability score and each claim's tower of cited lines (load-bearing, supporting, dead weight, phantom). "Pull the block" reruns the agent without those lines to test whether the claim falls. |
| **Agent log** tab | The run as a timeline: reasoning, tool calls and returned lines, or the raw log. |
| **Analyse an existing log** | Upload a `.log` file from an earlier run to check it. |

Runs are stored in `data/runs/<id>/`: the uploaded PDFs, `agent.log`, `report.json`,
`judge.json`, replay results and the full report.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask routes, background jobs (agent, judge, replays) and PDF validation. |
| `agent4.py` | The PDF agent. It logs the model's reasoning before each tool call, which Jenga's timing check needs, and the run's token usage and cost. |
| `llm.py` | Model clients, settings, prices and the `.env` loader. |
| `jenga.py` | Claim splitting, the static checks and the full report. Also a CLI: `python jenga.py some.log --out out`. |
| `judge.py` | The Haiku judge: atomic facts per claim; verdicts are counted from the facts in code. |
| `replay.py` | The causal tests behind "Pull the block". |
| `webview.py` | Turns a log into the timeline and renders the answer with citation chips. |
| `templates/`, `static/` | The UI (Jinja templates, CSS with light and dark themes, a little JS). |
| `test_jenga.py`, `test_replay.py` | Offline tests with scripted fake agents; no API calls. |

## How the traceability score works

Each claim starts at 100 and loses points for each issue below; the answer's score is the average.

| Signal | What it checks |
|---|---|
| Grounding | Was each cited line actually shown to the agent? Cited lines it never saw are phantom citations. |
| Evidence share | How many of the claim's specific terms (names, values, technologies) appear in the cited lines, elsewhere in the sources, or in no source (the model's own knowledge). |
| Bridging | On a fact question, the question's focus is in the claim but not in the citation. On an opinion question ("a good fit for Palantir?") the focus is the opinion's target and is not counted. |
| Jenga pull | Leave-one-out over the cited lines: *load-bearing* lines are the only source of some term; *dead weight* lines add nothing. |
| Timing | The agent wrote a value from the documents before any tool result contained it. Combined with a weak citation it marks the claim as post-rationalised. |
| Selection | Whether the cited lines match the evidence the agent selected. |

The verdicts are **grounded**, **post-rationalisation risk**, **post-rationalised**,
**phantom citation** and **uncited**. These checks compare words: they show whether a citation
*can* carry its claim. Only a replay ("Pull the block") tests whether the answer depended on it.
