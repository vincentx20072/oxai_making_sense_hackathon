# Jenga: ask about PDFs, then check whether the answer's citations hold up

Jenga is a Flask web app. You upload PDFs and write a prompt, and an AI agent answers with
line-level citations. **Jenga** then checks each claim in the answer for *post-rationalisation*:
a citation that supports the claim without having actually produced it. It follows the Jenga role
in the *Alibi + Jenga* project plan: Alibi asks "was it there?", Jenga asks "did it use it?".

> **No replays.** The causal replay tests in `replay.py` rerun the agent many times per claim, so
> they cost a lot of API calls. Neither the web app nor the CLI runs them. Every trust score here
> comes from static analysis of the agent's log, and needs **zero extra API calls**.

## Run it

```bash
pip install -r requirements.txt
```

```bash
export GROQ_API_KEY=...
```

```bash
python app.py
```

Then open http://localhost:5000.

| Page | What you get |
|---|---|
| **New question** | Drag in PDFs (PDF only, up to 10 files and 25 MB), write a prompt, and run. While the agent works, the page updates live with its steps and reasoning. |
| **Answer** tab | The answer, with citation chips. Hover a chip to see the cited lines, and click it to open the PDF page. It also shows the evidence the agent selected and the trust score. |
| **Trust** tab | Run Jenga by button, or switch it on to run automatically after the agent finishes. It gives a trust score, a verdict for each claim, and the full infographic. |
| **Agent log** tab | The agent's run as a timeline: reasoning, tool calls and returned lines. Lines later cited are highlighted. You can switch to the raw log or download it. |
| **Analyse an existing log** | Upload a `.log` file from an earlier run to score it. |
| **History** (sidebar) | Every run with its trust score. "Ask again with these files" reuses a run's PDFs. |

Runs are stored in `data/runs/<id>/`. Each run folder holds the uploaded PDFs, `agent.log`, the
trace, `report.json` and the infographic.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask routes, background agent jobs (2 at a time) and PDF validation. |
| `webview.py` | Turns a log into the timeline, and renders the answer with citation chips. |
| `templates/`, `static/` | The UI (Jinja templates, CSS with light and dark themes, a small amount of JS). |
| `agent4.py` | The PDF agent. It logs the model's reasoning before each tool call, which Jenga's timing check needs. |
| `jenga.py` | The analysis and the infographic. It can also be used from the command line: `python jenga.py some.log --out out`. |
| `replay.py` | The causal tests. They are deliberately not called by the app or the CLI. |
| `test_jenga.py` | Offline tests with scripted fake agents. Run them with `python test_jenga.py`. They make no API calls. |

## How the trust score works

Each claim starts at 100 and loses points for each issue below. Every deduction is shown with its
reason, and the answer's score is the average over its claims.

| Signal | What it checks |
|---|---|
| Grounding | Was each cited line actually shown to the agent? Cited lines it never saw are phantom citations. |
| Evidence share | How many of the claim's specific terms (names, values, technologies) appear in the cited lines. Terms found only elsewhere in the sources, or in no source (the model's own knowledge), cost points. |
| Bridging | The question's focus is in the claim but not in the citation. For example, "FastAPI, a Python framework" cites lines that never say Python. |
| Jenga pull | A leave-one-out over the cited lines. *Load-bearing* lines are the only source of some term; *dead weight* lines contribute nothing. |
| Timing | The agent wrote a value before any tool result contained it ("searching to confirm"). On its own this is a risk; combined with a weak citation it marks the claim as post-rationalised. |
| Selection | Whether the cited lines match the evidence the agent selected. |

The verdicts are **grounded**, **post-rationalisation risk**, **post-rationalised**,
**phantom citation** and **uncited**.

These checks compare words. They show whether a citation *can* carry its claim, not that the model
causally relied on it; only the replays could show that.
