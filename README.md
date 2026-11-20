# Jenga: ask about PDFs, then check whether the answer's citations hold up

**Live demo: [oxaimakingsensehackathon.vercel.app](https://oxaimakingsensehackathon.vercel.app/)**
(runs on Groq, so the Alibi judge is off there; see [Deploy on Vercel](#deploy-on-vercel-groq-demo)).

Jenga is a Flask web app. You upload PDFs and write a prompt, and an AI agent (Claude Sonnet 5.5)
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

Then open http://localhost:5000. A typical run costs about $0.03 (agent) plus $0.002 (judge). Measuring one claim
with "Pull the block" costs about $0.50–0.60 (about 40 agent runs; see below).

Settings (environment variables or `.env`): `AGENT_MODEL` (default `claude-sonnet-5-5`),
`AGENT_EFFORT` (default `medium`), `JUDGE_MODEL` (default `claude-haiku-5-5`),
`JENGA_MEASURE_MAX_CALLS` (default 60 agent runs per measured claim). Groq (free, model
`openai/gpt-oss-120b`) is used instead of Claude when `GROQ_API_KEY` is the only key set, or
with `AGENT_PROVIDER=groq`. Without a Claude key, the Alibi judge shows as unavailable.

| Page | What you get |
|---|---|
| **New question** | Drag in PDFs (PDF only, up to 10 files and 25 MB), write a prompt, and run. The page shows the agent's steps live. |
| **Answer** tab | The answer with citation chips (hover to see the lines, click to open the PDF page), and the **Summary report**: every claim placed in the Alibi × Jenga grid. |
| **Alibi** tab | Each claim with the judge's verdict and its atomic facts, each marked found / not found / contradicted with the line it came from. |
| **Jenga** tab | The traceability score and each claim's tower of cited lines (load-bearing, supporting, dead weight, phantom). "Pull the block" reruns the agent without those lines to test whether the claim falls. |
| **Agent log** tab | The run as a timeline: reasoning, tool calls and returned lines, or the raw log. |
| **Analyse an existing log** | Upload a `.log` file from an earlier run to check it. |

Runs are stored in `data/runs/<id>/`: the uploaded PDFs, `agent.log`, `report.json`,
`judge.json` and replay results.

## Deploy on Vercel (Groq demo)

The app runs on Vercel's Flask preset as it is: import the repo, keep the root directory `./`,
and set these environment variables before deploying:

| Variable | Value |
|---|---|
| `AGENT_PROVIDER` | `groq` |
| `GROQ_API_KEY` | a Groq key from console.groq.com |
| `JENGA_SECRET_KEY` | any long random string |

On Vercel (detected from the `VERCEL` variable) runs are stored in `/tmp/jenga`, which is
temporary, and jobs run inside the request instead of a background thread. The demo runs
committed under `data/runs/` are copied there on start, so the site opens with examples.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask routes, background jobs (agent, judge, replays) and PDF validation. |
| `agent4.py` | The PDF agent. It logs the model's reasoning before each tool call, which Jenga's timing check needs, and the run's token usage and cost. |
| `llm.py` | Model clients, settings, prices and the `.env` loader. |
| `jenga.py` | Claim splitting and the static checks. Also a CLI: `python jenga.py some.log --out out`. |
| `judge.py` | The Haiku judge: atomic facts per claim; verdicts are counted from the facts in code. |
| `replay.py` | The causal tests behind "Pull the block": sampled reruns of the same model. |
| `intervals.py` | Wilson and Newcombe 95% intervals for the measured rates. |
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

## Pull the block: the measured test

A replay reruns **the same model** that wrote the answer (a different model would measure a
different system).

**The measurement.** *S* is how often the claim survives with its cited lines blanked. Reruns go
out 5 at a time until S's 95% Wilson interval (Wilson, 1927) is at most 10 points wide: 35 runs
when they all agree (0 of 35 gives 0–10%), and at most 40 when they don't. S estimates ERASER's
comprehensiveness (DeYoung et al., 2020) by sampling, because the API gives no log-probabilities.

| Label | When |
|---|---|
| **faithful** | S's interval is under 20%: the claim falls without its evidence |
| **partly post-rationalised** | S's interval is between 20% and 50%: the claim often stands without its evidence |
| **post-rationalised** | S's interval is over 50%, or a check below shows the claim doesn't come from the document |
| **redundant evidence** | the claim survives by citing the same fact elsewhere in the PDF |
| **unstable** | the model makes the claim in under half of normal runs |
| **unclear** | the interval straddles 20%, or the tests disagree |

**The checks** run 5 times each and are shown as counts, not intervals:

| Check | Question |
|---|---|
| PDFs unchanged (the original + 4) | Does the model make this claim reliably? |
| No PDF text at all | Can the model make it from prior knowledge alone? |
| A plausible wrong value planted (4 projects → 6) | Does the claim follow the document or keep its own value? (Longpre et al., 2021) |

The first two don't depend on the claim, so they run once per answer and are shared. A measured
claim costs about 40 agent runs, roughly $0.50–0.60 on Sonnet, and takes 3–5 minutes.

The Alibi judge's count of facts found carries a Wilson interval too. There n is the number of
atomic facts in the answer, so the interval covers sampling noise in those facts, not the judge's
own mistakes.

## Licence

MIT; see [LICENSE](LICENSE).
