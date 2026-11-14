# PLAN — Jenga: Cited but Not Used?

> Built on the newest branch of `vincentx20072/oxai_making_sense_hackathon`: **`origin/Chris`** (commit `253a55a`, "It works claude is my goat", 2026-11-13 16:37, author MarcoBonato2007). It already contains the instrumented PDF agent (`agent4.py`), static Jenga scoring (`jenga.py`), the causal replay engine (`replay.py`, not yet called anywhere), the Flask web app (`app.py`, `webview.py`, `templates/`, `static/`) and 5 passing offline tests (`python test_jenga.py`, checked 2026-11-14 19:15).
> `origin/Marco` (`1496f48`, 14:39) is an earlier version of `agent4.py` that `origin/Chris` replaces ("Changes from the original agent4.py…" in its docstring). **Don't merge Marco**. `main` and `Julie` both point to `3316251`, which only has the README.
> Now: **Sat 2026-11-14 19:20 Europe/London.** Hard close: **Sun 2026-11-15 00:00** (team's answer; the organiser source is still to be confirmed). That leaves about 4 h 40 m.

---

## 0. Hook

Anyone who relies on an AI research agent sees an answer full of neat citations and assumes the agent *used* them. Jenga watches the agent read the PDFs, then pulls one block out of the tower. It blanks the cited line, or quietly changes the value in it, and replays the agent. If the answer survives and **still cites the line that now says something else**, the citation was decoration: post-rationalised. You get a trust score for each claim, a timeline of what the agent saw and when, and a live counterfactual that shows the difference.

**Why judges will remember it:** the same agent, the same pipeline and two PDFs. On an invented fact it follows the planted value (faithful). On a fact it already knew, it keeps its prior and cites a line that now contradicts it (caught live, on screen).

---

## 1. Track lock and sponsor alignment

**Main track:** "Read the machine" (partner: Intelligent Internet). **[VERIFY verbatim]** We don't have the organiser's wording. The team's own notes describe it as: *show what an agent did on open models / agent stacks: trace tool calls, attribute claims, flag guesses.*

Why this track: the product already does all three. `agent4.py` traces tool calls and the model's own reasoning before each call. `jenga.py` attributes each claim to cited lines. The timing signal flags values the model asserted before any tool returned them ("guesses"). There are no other tracks in our material, so there are none to reject. **[VERIFY]** whether other tracks exist and whether entries can stack.

| Bounty or prize | Prize | Exact requirement (quoted) | How we meet it | Demo step where it is visible |
|---|---|---|---|---|
| Read the machine (main) | **[VERIFY]** | **[VERIFY]**: no organiser text in our material. Team paraphrase: "trace tool calls, attribute claims, flag guesses" | Trace: `agent4.py` writes `agent.log`, `trace.jsonl` and `answer.json` per run, and logs `MODEL REASONING` before every tool call. Attribute: `jenga.split_claims` + `parse_citations` give line-level `[file, P1-L11:L13]` chips. Flag guesses: Jenga's timing signal (`asserted_before_seen`) | Demo steps 2–4 (live run timeline, citation chips, timing flags in the Trust tab) |
| "Open models / agent stacks" (part of the same track wording, **[VERIFY]**) | n/a | **[VERIFY]** | Default agent model is `openai/gpt-oss-120b` on Groq, an open-weights model (it's the `MODEL` constant in `agent4.py`). Stretch: Intelligent Internet's own `Intelligent-Internet/II-Search-4B` (Qwen3-4B base, Apache-2.0) run locally through Ollama's OpenAI-compatible endpoint | Demo step 1 (model badge on the run page); stretch step 6 (the same question on II-Search-4B) |
| Intelligent Internet partner recognition (if any) | **[VERIFY]** | **[VERIFY]** | The proposal (§9.3) compares II-Search-4B with base Qwen3-4B. We show at least one II-Search-4B run, or record honestly why its tool calling failed | Write-up "Models" section, and demo step 6 if it works |

**Technical shortcuts (most visible effect for least code):**
- **Reasoning in the trace.** Groq returns gpt-oss reasoning in `message.reasoning` by default (`reasoning_format` / `include_reasoning`, gpt-oss only, per console.groq.com/docs/reasoning). `agent4.py` already reads `getattr(message, "reasoning", None)`. Nothing to build; just point at it in the demo.
- **Causal replay is already written.** `replay.Replayer.run(hidden=…, plants=…)` and `replay_report()` exist. We only need a one-claim entry point with a cost cap, a cache, and a button.
- **Plausible plants.** `replay_report(…, plant_overrides={"1870": "1874"})` already takes overrides. Using hand-picked small plants for the demo avoids the default `n * 1.5` year (1870 → 2805, which is implausible).
- **Local II-Search-4B:** `ollama run hf.co/gabriellarson/II-Search-4B-GGUF:Q4_K_M` (community GGUF listed on Hugging Face), then `AGENT_BASE_URL=http://localhost:11434/v1`. **[VERIFY]** whether tool calling works through Ollama for this model.

---

## 2. Critical checkpoints

| Time | What | Consequence if missed |
|---|---|---|
| 19:30 | `GROQ_API_KEY` confirmed working on every laptop that runs the agent (`python -c "import agent4; agent4.make_client().models.list()"`) | No live runs; demo only from cached runs |
| 19:30 | Go / no-go on the AgentSync workspace (Prompt 2). Only "go" if it can be live by **20:00**, ideally on an existing hub | Setup eats build time. If no-go: same folders and owners, coordinate in group chat |
| **[VERIFY]** | Venue closes / we have to leave | Work moves online; whoever has the demo laptop must be able to record from home |
| **[VERIFY]** | Judging / pitch slot (live or video-only?) | Pitch prep has to move earlier |
| 21:15 | **Internal: end-to-end loop works** on `Vincent` (see §6) | Cut the eval run and II-Search; keep only the demo |
| 22:30 | **Internal: feature freeze.** Only fixes after this | — |
| 23:00 | **Internal: demo video recorded** (plan B) | No plan B if the live demo or Groq fails |
| **23:15** | **Internal: submission complete** (45 min before the hard close) | — |
| **00:00 Sun 15 Nov** | **Hard close: submission** (team-stated, **[VERIFY]** exact time and portal) | Disqualified / not judged |

**Mandatory deliverables as the organisers state them:** **[VERIFY]**. We don't have the organiser's list. Assume until told otherwise: a repo link, a short write-up, and a demo video of 2 minutes or less. Ask at the desk (see §10).

---

## 3. Hard rules and disqualifiers

None of these come from organiser text; every item is **[VERIFY]** until someone reads the rules page or asks.
1. **Eligibility:** team of 4 (Vincent, Marco, Chris, Julie). Team-size limit: **[VERIFY]**.
2. **Required tech:** the track wording mentions "open models / agent stacks" (team paraphrase). We use gpt-oss-120b (open weights) and optionally II-Search-4B. **[VERIFY]** whether closed-model use (for example Claude as a coding assistant) is allowed. It is not part of the product.
3. **Banned tech:** **[VERIFY]**.
4. **Pre-existing code:** the repo's first commit is `3316251` on 2026-11-13 12:49. **[VERIFY]** that this falls inside the hackathon window and that building on the team's own Friday work is allowed.
5. **Repo visibility:** **[VERIFY]** whether it must be public, and by when. The repo currently contains `ai_files/resume.pdf` and `data/runs/81518514ddfd/files/resume.pdf`, a real person's CV. Confirm consent, or swap in the demo PDFs before the repo goes public.
6. **Submission format:** **[VERIFY]** portal, fields and video length.
7. **Judging criteria and weights:** **[VERIFY]**. Default assumption: technical depth, track fit, demo quality, originality.
8. **Honesty rule we impose on ourselves:** label static Jenga scores as lexical and replay labels as behavioural, never as "the model's true reasoning". This matches proposal §3.3 and §14.1 and the README's last paragraph.

---

## 4. Architecture

**User loop**
1. Upload 1–10 PDFs (≤25 MB) and ask a question (`POST /runs`).
2. The agent (`agent4.run_agent`) lists, searches and reads the PDFs, selects evidence, and answers with line citations. The page streams its reasoning and tool calls live.
3. Jenga (`jenga.analyse_log_text`) splits the answer into claims and scores each one: grounding, evidence share, bridging, leave-one-out pull, timing, selection. It shows a verdict and trust score per claim.
4. **New:** for any claim, press **"Pull the block"**. `replay.replay_claim` reruns the agent with the cited span blanked, and with the claim's value planted to a plausible wrong one. The result is capped at `JENGA_REPLAY_MAX_CALLS` and cached.
5. **New:** the claim card shows the before/after answer, the outcomes (`follows` / `overrides and cites` / `drops` / `cites removed lines` …) and the causal label `faithful` / `post-rationalised` / `unclear`.
6. **New:** the `/results` page shows the 12-question pilot. It reports the evidence-insensitive supported-citation rate with a 95% Wilson CI, split by known vs invented facts, plus the AUROC of the static trust score at predicting the replay label (H1 and H3 from the proposal).

**Components and data flow**
```
browser ──POST /runs──▶ app.py ──thread──▶ agent4.run_agent ──▶ llm.make_client() ──▶ Groq | Ollama
                         │                    │ writes data/runs/<id>/{agent.log, trace/<id>/trace.jsonl, answer.json}
                         │◀── GET /api/runs/<id> (progress via webview.progress)
                         ├──POST /runs/<id>/analyse──▶ jenga.analyse_log_text ──▶ report.json, jenga.html
                         ├──POST /runs/<id>/replay {claim_id,…} (NEW) ──thread──▶ replay.replay_claim ──▶ agent4.run_agent ×k
                         │                                         └──▶ data/runs/<id>/replay/<claim_id>.json (cache)
                         └──GET /results (NEW) ◀── eval/results.json ◀── eval/run_eval.py (CLI, offline from the web app)
contracts.py (NEW): TypedDicts shared by every module above
```

**External APIs**

| API | Does | Env vars | Limits |
|---|---|---|---|
| Groq Chat Completions (`groq` SDK, model `openai/gpt-oss-120b`; fallback `openai/gpt-oss-20b`) | Agent runs and replays, with tool calling and reasoning | `GROQ_API_KEY` (server-only) | Paid price is about $0.15 / $0.60–0.75 per 1M tokens in/out. Free-tier RPM/TPM/RPD: **[VERIFY]** at console.groq.com/settings/limits |
| Ollama OpenAI-compatible endpoint (optional) | Local II-Search-4B | `AGENT_BASE_URL`, `AGENT_API_KEY` (any string for Ollama), `AGENT_MODEL` | Laptop CPU/GPU speed. Q4_K_M is about 2.5 GB **[VERIFY]** |

**Fallbacks for risky dependencies**

| Risk | Fallback that keeps the demo alive |
|---|---|
| Groq rate limit or latency during replay (one replay = claims × (spans + 2) × samples agent runs) | `replay_claim` runs **one claim** with `samples=1` and a call cap. Results are cached to disk, so the demo replays from cache with **0 API calls**. Pre-run the demo claims before 22:30 |
| gpt-oss-120b down or slow | `AGENT_MODEL=openai/gpt-oss-20b` through the same client |
| II-Search-4B tool calling fails through Ollama | Write it up as a finding in `NOTES.md`. Keep gpt-oss as the demo model |
| Wifi or the live demo fails | Pre-recorded video (`demo/plan-b.mp4`, link in the README; don't commit it if it's over 50 MB) |
| The agent answers the demo question differently live | Demo runs are pre-created and kept in `data/runs/` (committed), so we open them by id |

---

## 5. Team split

| Role | Person | Owns (folders/files) | Deliverables | Depends on |
|---|---|---|---|---|
| Dev 1: lead, eval and stats | Vincent | `contracts.py`, `jenga.py`, `test_jenga.py`, `eval/`, `docs/results.md` | Scaffold + contract; eval harness; pilot numbers; Alibi 2×2 labels | — |
| Dev 2: agent and replay | Marco | `agent4.py`, `llm.py`, `replay.py`, `test_replay.py` | Pluggable client; `replay_claim()` with cap and cache; perturbation magnitudes; II-Search-4B stretch | `contracts.py` |
| Dev 3: web app | Chris | `app.py`, `webview.py`, `templates/`, `static/` | "Pull the block" button + causal panel; `/results` page; polish | `contracts.py`, `replay_claim` signature, `eval/results.json` shape |
| Dev 4: demo, pitch and submission | Julie | `demo/`, `README.md`, `docs/WRITEUP.md`, `samples/` | Demo PDFs and questions; cached demo runs; write-up; video; submission | `replay_claim` (to cache demo runs) |

Rules:
- One owner per folder or file.
- `contracts.py` is owned by Vincent. It changes only after a `CONTRACT contracts.py: <old> -> <new>` message gets his reply.
- Julie owns the demo, the pitch and the submission. Nobody else submits.
- `requirements.txt` is a hot file: append-only, and announce it.

**Staging folder `temp/` (two-agent agreement before anything enters the project):**
- Integration happens on **`Vincent`** (Vincent's branch, cut from `origin/Chris`). Nobody edits project files on `Vincent` directly except through promotion.
- Each agent writes its change into `temp/<task-id>-<slug>/` on its own task branch, and opens a PR into `Vincent`. The folder contains:
  - `MANIFEST.json`: `{"task": "T03", "author": "marco.laptop.claude-replay-claim", "files": {"temp/T03-replay-claim/replay.py": "replay.py", …}, "tests": "python test_replay.py"}`
  - the proposed files themselves (full copies of the files the task owns)
  - `PROPOSAL.md`: what changed, why, and the test output
- **A second agent** (a different identity, ideally the owner of a dependent module) reviews it in its own worktree. If it agrees, it posts `APPROVE <task>: <reason>` in the main room and adds `temp/<task-id>-<slug>/APPROVED_BY` containing its identity. If it doesn't, it posts `CHANGES <task>: <what>`.
- `python tools/promote.py temp/<task-id>-<slug>` refuses unless `APPROVED_BY` exists **and** differs from `author`. When both hold, it copies every file to its destination, runs `tests` and the full `test_cmd`, deletes the temp folder and prints the commit message. On test failure it rolls back. The PR into `Vincent` then contains the promoted files and no `temp/` folder.
- Contract changes (`contracts.py`) also go through `temp/`, and the approver must be Vincent's agent.
- After 22:30 (freeze), only fixes are promoted; anything still in `temp/` stays out of the submission.
- T01 (scaffold) is the only direct commit to `Vincent`, because it creates `temp/` and `tools/promote.py`. Already done in the setup session: branch `Vincent` with `temp/README.md`, `tools/promote.py` and `docs/PLAN.md` (see the final report for status).
- **Parallel agents from PowerShell:** `.\tools\spawn-agents.ps1 -Person vincent -Tasks T08,T10` opens one Claude Code window per task. Each window gets its own worktree in `..\oxai_making_sense_hackathon-agents\<task>-<slug>\` and its own branch `vincent/claude/<slug>` cut from `origin/Vincent`. Add `-Review` for one reviewer agent: it approves staged PRs from *other* agents and promotes them. `-List` shows tasks, and `-Cleanup` removes finished worktrees. Every teammate can run it on their own laptop with their own `-Person`.
- Budget about 10 minutes of review per promotion. The 21:15 and 22:30 checkpoints mean *promoted*, not just staged.

---

## 6. Timeline (Sat 14 Nov → Sun 15 Nov, Europe/London)

| Block | Who | Work | Done when … |
|---|---|---|---|
| 19:20–19:30 | all | Read this plan. Check the Groq key. Go/no-go on the AgentSync setup | Each person has posted "key OK" (or not) |
| 19:30–20:00 | Vincent | **T01 scaffold** on `Vincent` (branched from `origin/Chris`) | `pip install -r requirements.txt && python -m py_compile *.py eval/*.py && python test_jenga.py && python eval/test_eval.py` all pass; `POST /runs/<id>/replay` returns the stub JSON |
| 19:30–20:00 | Marco / Chris / Julie | Read the code they will own. Julie drafts the demo facts | Julie posts the two demo facts and the plants in chat |
| 20:00–21:15 | Marco | T02 `llm.py`, T03 `replay_claim` | `python -c "import replay; print(replay.replay_claim('<demo_run>','c1',max_calls=12)['causal']['label'])"` prints a label; the second call returns `cached: true` |
| 20:00–21:15 | Chris | T05 replay UI against the stub, then the real call | Clicking "Pull the block" on a claim shows the spinner, then the causal panel |
| 20:00–21:15 | Julie | T07 demo PDFs + questions + first runs | `demo/pdfs/known_fact.pdf` and `invented_fact.pdf` exist, and one run of each sits in `data/runs/` |
| 20:00–21:15 | Vincent | T08 eval harness (dry-run with the fake client) | `python eval/run_eval.py --dry-run` writes `eval/results.json`; `python eval/test_eval.py` passes |
| **21:15** | all | **End-to-end loop check** on `Vincent` | On Julie's laptop: upload `known_fact.pdf` → answer → Jenga → "Pull the block" on the year claim → label shown in the UI |
| 21:15–22:30 | Vincent | T09 real eval run (12 questions); T10 Alibi labels if time | `docs/results.md` holds the rate, CI and AUROC from the real run |
| 21:15–22:30 | Marco | T04 magnitudes; T11 II-Search-4B (stretch) | `python test_replay.py` passes; one II-Search run, or a NOTES.md entry |
| 21:15–22:30 | Chris | T06 `/results` page; UI polish | `/results` renders the table and chart from `eval/results.json` |
| 21:15–22:30 | Julie | T12 README; T13 write-up draft | Write-up has every section of §7; README "Run it" works on a clean clone |
| **22:30** | all | **Feature freeze**: PRs merged into `Vincent`; Julie pre-runs and caches every replay used in the demo | Demo click-through needs 0 live API calls |
| 22:30–23:00 | Julie (+Vincent narrates) | T14 record the demo video | `demo/plan-b.mp4` plays end to end, 90 seconds or less |
| 23:00–23:15 | Julie | T15 merge `Vincent` → `main`, tag `v1.0`, submit | Portal shows the submission as received; screenshot posted in chat |
| 23:15–00:00 | all | Buffer. Fixes only on a hotfix branch, re-tagged | — |

---

## 7. Demo and pitch

**20-second hook (spoken):**
"AI agents cite sources. A correct citation doesn't prove the agent used it. Jenga pulls one block out from under a citation and watches whether the answer falls. Here's the same agent, the same pipeline and two documents. Watch what happens when it already thinks it knows the answer."

**60–90 second live demo:**
1. (0:00) On the history page, open pre-created run **A: invented fact** ("When was Veltrix Analytics founded?" → `invented_fact.pdf` says 2011). Point at the model badge `openai/gpt-oss-120b`.
2. (0:10) **Agent log tab:** the timeline shows its reasoning before each search; the cited line is highlighted.
3. (0:20) **Answer tab:** hover the citation chip to see line P1-L4.
4. (0:30) **Trust tab:** the static verdict is grounded. Press **Pull the block** (cached). The plant 2011 → 2013 gives "follows" and the label is **faithful**.
5. (0:45) Open run **B: known fact**. `known_fact.pdf` is our own one-page note stating a well-known date (for example Keble College founded in 1870). The static verdict looks grounded too.
6. (0:55) **Pull the block:** the plant 1870 → 1874 gives "overrides and cites". The agent still says 1870 and cites the line that now says 1874, so the label is **post-rationalised**. The trust score drops.
7. (1:10) **/results:** across 12 pilot questions, post-rationalised citations show up on known facts and (expected) much less on invented ones, with a CI. One sentence on AUROC: how far the free static score predicts the expensive replay.
8. (1:25) Close: "Citation correctness overestimates evidence use. Jenga measures the gap."

**Demo dataset and inputs:**
- `demo/make_demo_pdfs.py` builds one-page PDFs from text we write ourselves (no copyright issues), each with numbered short lines:
  - `known_fact.pdf`: 4–6 lines about a widely known entity, with one date the model surely knows.
  - `invented_fact.pdf`: the same structure about an invented company.
  - Optional third: `two_sources.pdf` for redundancy (the same fact on two lines).
- `demo/questions.json`: `{id, pdf, question, value, plant}` with hand-picked **small** plants (±2–4 years).
- Pre-run both questions until each gives a clean single-claim answer. Keep those run ids in `demo/RUNS.md`.

**Plan B:** `demo/plan-b.mp4`, a screen recording of steps 1–8 made at 22:30–23:00 from cached runs, linked at the top of the README.

**Write-up outline (`docs/WRITEUP.md`):**
1. Problem: citation correctness ≠ citation faithfulness (proposal §1.2–1.3)
2. What we built: agent trace → Jenga static scores → counterfactual replay → pilot
3. How the scores work (README table), and what they do *not* claim
4. Pilot: setup (12 questions, known vs invented, small plants, `samples=1`), results with CIs and AUROC, caveats (n is small, single model, lexical matching)
5. Models: gpt-oss-120b; II-Search-4B attempt
6. Next: multi-source tasks (proposal §6), decision-preserving interventions (§7.2), logprob-based Jenga (§4.4, needs model access **[VERIFY]**), paper plan
7. Team and links

---

## 8. CLAUDE.md

```markdown
# Project: Jenga — Cited but Not Used? (OxAI Making Sense Hackathon — Read the machine)

## Core Concept
1. User uploads PDFs and asks a question (POST /runs).
2. agent4.run_agent answers with line-level citations [file, P1-L4:L6]; every tool call, the model's reasoning, and selected evidence are logged to data/runs/<id>/.
3. jenga.analyse_log_text splits the answer into claims and gives each a static verdict + score (grounding, evidence share, bridging, leave-one-out pull, timing, selection).
4. "Pull the block": replay.replay_claim reruns the agent with the cited span blanked and with the claim's value planted; result cached in data/runs/<id>/replay/<claim_id>.json.
5. The claim card shows the causal label: faithful | post-rationalised | unclear.
6. /results shows the pilot: evidence-insensitive supported-citation rate (Wilson 95% CI), by family (known vs invented), and AUROC of the static score vs the replay label.

## Target Bounties (Do Not Deviate From Stack)
- Track "Read the machine" (Intelligent Internet): trace tool calls, attribute claims, flag guesses [VERIFY exact wording].
- Agent model: `openai/gpt-oss-120b` on Groq via the `groq` Python SDK (`from groq import Groq`). Fallback `openai/gpt-oss-20b`. Reasoning text comes from `message.reasoning` (Groq default for gpt-oss).
- Optional open model: `Intelligent-Internet/II-Search-4B` via Ollama (`hf.co/gabriellarson/II-Search-4B-GGUF:Q4_K_M`) behind `AGENT_BASE_URL` (OpenAI-compatible). [VERIFY tool calling]
- Replays are opt-in only, per claim, capped by `JENGA_REPLAY_MAX_CALLS` (default 12), and cached. Never call replay_report() from the web app.
- Plants in the demo are hand-picked small perturbations (plant_overrides), never the default n*1.5.

## Tech Stack
Python 3.11+ (tested on 3.13), Flask >=3, pypdf >=4, groq >=0.9; fpdf2 only for demo/make_demo_pdfs.py. Plain Jinja templates + CSS + small vanilla JS. No database: JSON files under data/runs/. Tests: plain-assert scripts (python test_jenga.py, python test_replay.py, python eval/test_eval.py), no network.

## Directory Structure & Ownership Boundaries
- `temp/` [Dev 1: lead, process only] — staging area. Every change lands first in `temp/<task-id>-<slug>/` with `MANIFEST.json` {task, author, files: {temp path: destination}, tests}, `PROPOSAL.md`, and the proposed files. A second agent with a different identity reviews it and writes `APPROVED_BY`; only then does `python tools/promote.py temp/<task-id>-<slug>` copy the files into place, run the tests (rolling back on failure) and delete the folder. `temp/README.md` explains this.
- `tools/promote.py` [Dev 1] — the promotion script described above; refuses without `APPROVED_BY` ≠ author.
- `contracts.py` [Dev 1: lead] — TypedDicts shared by all modules: `TraceStep`, `EvidenceSelection`, `AgentAnswer`, `CitedLine`, `Deduction`, `Verdict` (Literal), `Claim`, `JengaReport`, `Magnitude` (Literal small|medium|large), `ReplayRequest`, `RemovalResult`, `PlantResult`, `CausalLabel` (Literal faithful|post-rationalised|unclear), `CausalResult`, `ReplayResult`, `EvalQuestion`, `EvalRow`, `EvalSummary`. Changes only via `CONTRACT contracts.py: old -> new` + reply from Dev 1.
- `jenga.py` [Dev 1] — log parser, claim splitter, static scoring, infographic (`analyse_log_text`, `analyse_run`, `render_html`, CLI `python jenga.py x.log --out out`). Adds `claim["alibi"]` (supported|unsupported|phantom) in T10.
- `test_jenga.py` [Dev 1] — offline tests with FakeClient.
- `eval/questions.jsonl` [Dev 1] — 12 EvalQuestion rows (6 known, 6 invented).
- `eval/pdfs/` [Dev 1] — generated one-page PDFs for eval questions.
- `eval/run_eval.py` [Dev 1] — runs original + plant + remove-all per question (`--dry-run` uses FakeClient), writes `eval/results.jsonl` and `eval/results.json` (EvalSummary).
- `eval/stats.py` [Dev 1] — `wilson_ci(k, n)`, `auroc(scores, labels)`, pure Python.
- `eval/test_eval.py` [Dev 1] — stats + dry-run tests.
- `docs/results.md` [Dev 1] — pilot numbers and caveats.
- `llm.py` [Dev 2: agent] — `make_client()` returns a Groq client, or an OpenAI-compatible client when `AGENT_BASE_URL` is set; `default_model()` reads `AGENT_MODEL`.
- `agent4.py` [Dev 2] — the agent loop `run_agent(question, *, files_dir, hidden_lines, plants, ...)`; uses `llm.make_client()`.
- `replay.py` [Dev 2] — existing `Replayer`, `replay_report`; NEW `replay_claim(run_id, claim_id, *, modes=("remove_all","plant"), plant_override=None, magnitude="small", samples=1, max_calls=None, client=None) -> ReplayResult`, `estimate_calls(report, claim_id, modes, samples) -> int`, `perturb(term, magnitude) -> str`.
- `test_replay.py` [Dev 2] — offline tests for replay_claim, cache and perturb.
- `app.py` [Dev 3: web] — Flask routes. NEW `POST /runs/<run_id>/replay` (json {claim_id, modes, plant_override}) starts a background job; `GET /api/runs/<run_id>/replay/<claim_id>` returns status/ReplayResult; `GET /results`.
- `webview.py` [Dev 3] — timeline + answer rendering.
- `templates/run.html` [Dev 3] — Trust tab: "Pull the block" button, cost estimate, causal panel. `templates/results.html` NEW. `templates/base.html` adds a Results link.
- `static/app.js`, `static/app.css` [Dev 3] — polling for replay status, panel styles (light + dark).
- `demo/make_demo_pdfs.py` [Dev 4: demo] — builds `demo/pdfs/*.pdf` from text.
- `demo/questions.json` [Dev 4] — demo questions with value + plant.
- `demo/RUNS.md` [Dev 4] — ids of pre-created demo runs and cached replays.
- `data/runs/<id>/` [Dev 4 for demo runs] — committed demo runs only; other runs are gitignored.
- `README.md`, `docs/WRITEUP.md` [Dev 4].
- `samples/` [Dev 4] — sample logs and reports.

## Environment Variables
- `GROQ_API_KEY` — server-only.
- `AGENT_BASE_URL` — optional, OpenAI-compatible endpoint (e.g. http://localhost:11434/v1). Server-only.
- `AGENT_API_KEY` — optional, key for AGENT_BASE_URL. Server-only.
- `AGENT_MODEL` — optional, overrides `openai/gpt-oss-120b`.
- `JENGA_REPLAY_MAX_CALLS` — optional, default 12: max agent runs per replay request.
All are server-side; nothing is exposed to the browser.

## Hard Rules
1. Submit by 23:15 Sat 14 Nov (hard close 00:00 Sun 15 Nov, Europe/London) — Julie submits.
2. Only the opt-in, capped, cached per-claim replay may call the API beyond the agent run itself.
3. Never claim Jenga reveals the model's internal reasoning: static = lexical, replay = behavioural.
4. No secrets, `.env`, or real people's CVs in git; demo uses PDFs we wrote.
5. One owner per file; contracts.py changes only via announced CONTRACT. Nothing enters the project on branch `Vincent` except via `temp/` + a second agent's `APPROVED_BY` + `tools/promote.py`.

## Immediate Next Step
On branch `Vincent` (Vincent's branch, matching the repo's per-person branch names) created from `origin/Chris` (do NOT merge origin/Marco): add `.gitignore` (`__pycache__/`, `.env`, `.env.*`, `data/runs/*` except demo runs listed in demo/RUNS.md, `runs/`, `replays.log`) and `git rm -r --cached __pycache__`; write `contracts.py` with complete TypedDicts matching the current report.json keys (`id, text, citations, verdict, score, deductions, E_lex, precision, terms, bridging_terms, timing_terms, cited_lines, causal`; cited line keys `file, page, line, text, status, selected, terms, unique_terms, role`); add stubs that import and run: `llm.py` (make_client/default_model, Groq default), `replay.replay_claim`/`estimate_calls`/`perturb` (return a ReplayResult with label "unclear", calls_used 0), `test_replay.py`, `eval/__init__.py`, `eval/stats.py` (real wilson_ci + auroc — they are small), `eval/run_eval.py --dry-run`, `eval/test_eval.py`, `eval/questions.jsonl` (2 example rows), `demo/` with README placeholder, Flask routes `POST /runs/<id>/replay` + `GET /api/runs/<id>/replay/<claim_id>` + `GET /results` returning stub data, `templates/results.html`, `temp/README.md` + `temp/.gitkeep`, and `tools/promote.py` (working, with a self-test `python tools/promote.py --self-test`). Then `pip install -r requirements.txt && python -m py_compile *.py eval/*.py && python test_jenga.py && python test_replay.py && python eval/test_eval.py` must pass, and `python app.py` must serve `/`, `/results`. Push `Vincent`; all four people pull and start.
```

---

## 9. Setup handoff

```yaml
event:   {name: "OxAI Making Sense Hackathon", url: "", submission_url: "", timezone: "Europe/London"}
project: {name: "Jenga — Cited but Not Used?", slug: "jenga-cited"}
deadlines:
  - {at: "2026-11-14T20:00", what: "Scaffold + contracts.py on Vincent; AgentSync go/no-go deadline", hard: false}
  - {at: "2026-11-14T21:15", what: "End-to-end loop: upload -> answer -> Jenga -> Pull the block -> label in UI", hard: false}
  - {at: "2026-11-14T22:30", what: "Feature freeze; demo replays cached", hard: false}
  - {at: "2026-11-14T23:00", what: "Demo video recorded (plan B)", hard: false}
  - {at: "2026-11-14T23:15", what: "Submission complete", hard: false}
  - {at: "2026-11-15T00:00", what: "Hard close: submission", hard: true}
product_repo:
  mode: new                            # repo already exists: vincentx20072/oxai_making_sense_hackathon — skip create; create branch Vincent from origin/Chris; all task work staged in temp/ and promoted only after a second agent approves
  upstream: null
  integration_branch: Vincent
  clean_submission: false
  must_be_public_by: null
stack:   {language: "python", framework: "flask", package_manager: pip, install_cmd: "pip install -r requirements.txt", dev_cmd: "python app.py", build_cmd: "python -m py_compile *.py eval/*.py", test_cmd: "python test_jenga.py && python test_replay.py && python eval/test_eval.py && python tools/promote.py --self-test"}
env_vars: [GROQ_API_KEY, AGENT_BASE_URL, AGENT_API_KEY, AGENT_MODEL, JENGA_REPLAY_MAX_CALLS]
contract_file: "contracts.py"
rooms:
  - {name: "jenga-cited",        purpose: "plan, task claims, START/COMMIT/PR/BLOCKED/DONE, CONTRACT, APPROVE/CHANGES for temp/ promotions"}
  - {name: "jenga-cited-replay", purpose: "agent4, llm client, replay_claim, plants, Groq limits, II-Search-4B"}
  - {name: "jenga-cited-eval",   purpose: "pilot questions, stats (Wilson CI, AUROC), results.md, Alibi labels"}
  - {name: "jenga-cited-demo",   purpose: "demo PDFs, cached runs, UI flow, write-up, video, submission"}
roles:
  - {id: dev1, title: "Lead, eval and stats", person: "vincent", owns: [contracts.py, jenga.py, test_jenga.py, eval/, docs/results.md]}
  - {id: dev2, title: "Agent and replay", person: "marco", owns: [agent4.py, llm.py, replay.py, test_replay.py]}
  - {id: dev3, title: "Web app", person: "chris", owns: [app.py, webview.py, templates/, static/]}
  - {id: dev4, title: "Demo, pitch and submission", person: "julie", owns: [demo/, README.md, docs/WRITEUP.md, samples/]}
hot_files: [contracts.py, requirements.txt, app.py, .gitignore, NOTES.md, tools/promote.py]
tasks:
  - {id: T01, slug: scaffold, title: "branch Vincent from origin/Chris; temp/ staging folder + tools/promote.py; .gitignore; contracts.py; stubs for llm, replay_claim, eval, demo, replay+results routes", role: dev1, scope: [contracts.py, .gitignore, llm.py, replay.py, test_replay.py, eval/, demo/, app.py, templates/results.html, temp/, tools/promote.py], depends: [], done_when: "install, py_compile, test_jenga, test_replay, eval/test_eval, promote --self-test all pass; python app.py serves / and /results; POST /runs/<id>/replay returns stub JSON"}
  - {id: T02, slug: llm-client, title: "llm.make_client/default_model with AGENT_BASE_URL/AGENT_MODEL; agent4 uses it", role: dev2, scope: [llm.py, agent4.py], depends: [T01], done_when: "python test_jenga.py passes; with AGENT_BASE_URL unset a real run uses Groq gpt-oss-120b; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T03, slug: replay-claim, title: "replay.replay_claim for one claim with call cap, estimate_calls, disk cache", role: dev2, scope: [replay.py, test_replay.py], depends: [T01], done_when: "test_replay.py passes offline; on a demo run replay_claim returns a CausalResult label, second call returns cached true with calls_used 0; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T04, slug: perturb-magnitude, title: "perturb(term, magnitude) small/medium/large for years, numbers, names; recorded in PlantResult", role: dev2, scope: [replay.py, test_replay.py], depends: [T03], done_when: "test_replay.py asserts perturb('1870','small') in 1866..1874 and != 1870; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T05, slug: replay-ui, title: "Trust tab Pull the block button, cost estimate, polling, causal panel", role: dev3, scope: [app.py, templates/run.html, static/app.js, static/app.css], depends: [T01], done_when: "clicking the button on a claim shows spinner then causal label and before/after answer, using a cached replay; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T06, slug: results-page, title: "/results page rendering eval/results.json table + bar chart with CI", role: dev3, scope: [app.py, templates/results.html, templates/base.html, static/app.css], depends: [T01], done_when: "/results renders from eval/results.json produced by run_eval --dry-run and by the real run; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T07, slug: demo-dataset, title: "demo PDFs (known_fact, invented_fact), questions.json, pre-created runs in demo/RUNS.md", role: dev4, scope: [demo/, data/runs/], depends: [T01], done_when: "both demo runs open from History and each has a single-claim cited answer; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T08, slug: eval-harness, title: "eval/questions.jsonl (6 known, 6 invented), run_eval.py with --dry-run, stats.py", role: dev1, scope: [eval/], depends: [T01], done_when: "python eval/run_eval.py --dry-run writes eval/results.json; python eval/test_eval.py passes; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T09, slug: eval-run, title: "real 12-question pilot on Groq; docs/results.md", role: dev1, scope: [eval/, docs/results.md], depends: [T03, T08], done_when: "eval/results.json from a real run is committed and docs/results.md reports rate, Wilson CI by family, AUROC; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T10, slug: alibi-labels, title: "claim alibi field supported/unsupported/phantom + proposal 2x2 table in report", role: dev1, scope: [jenga.py, test_jenga.py, contracts.py], depends: [T01], done_when: "test_jenga.py asserts alibi on honest and rationaliser agents; jenga.html shows the 2x2; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T11, slug: ii-search-local, title: "run II-Search-4B through Ollama via AGENT_BASE_URL (stretch)", role: dev2, scope: [llm.py, NOTES.md], depends: [T02], done_when: "one II-Search-4B run in data/runs/ or a NOTES.md entry explaining the failure; staged in temp/, APPROVED_BY a second agent, promoted with tools/promote.py"}
  - {id: T12, slug: readme, title: "README: replay button, results page, demo, env vars; remove CV PDFs if no consent", role: dev4, scope: [README.md, ai_files/, samples/], depends: [T05], done_when: "fresh clone + README steps reach a working demo run"}
  - {id: T13, slug: writeup, title: "docs/WRITEUP.md per PLAN section 7", role: dev4, scope: [docs/WRITEUP.md], depends: [T09], done_when: "all 7 sections filled with real numbers from docs/results.md"}
  - {id: T14, slug: demo-video, title: "record 90s plan-B video from cached runs", role: dev4, scope: [demo/RUNS.md], depends: [T05, T07], done_when: "video plays end to end, link in README"}
  - {id: T15, slug: submit, title: "merge Vincent to main, tag v1.0, submit on portal", role: dev4, scope: [README.md], depends: [T12, T13, T14], done_when: "portal confirms submission before 23:15; screenshot posted in main room"}
```

---

## 10. Open questions and [VERIFY] list

| # | Question | Ask whom |
|---|---|---|
| 1 | Exact "Read the machine" track text, prizes, and whether other tracks or bounties stack | Organiser desk / Intelligent Internet rep |
| 2 | Exact hard close (00:00 assumed), submission portal URL, required fields, video length | Organisers |
| 3 | Mandatory deliverables (repo? video? slides? write-up?) | Organisers |
| 4 | Judging criteria and weights; live pitch or video only; slot time | Organisers |
| 5 | Venue closing time tonight | Organisers |
| 6 | Pre-existing code rule: is Friday 13 Nov work (`3316251` onwards) inside the window? | Organisers |
| 7 | Must the repo be public, and by when? Is it public now? (The setup session couldn't read repo metadata) | Organisers / Vincent (`gh repo view`) |
| 8 | Consent for `resume.pdf` (real CV) in `ai_files/` and `data/runs/81518514ddfd/`; otherwise remove it before it goes public | Marco (who committed it) |
| 9 | Groq free-tier limits for `openai/gpt-oss-120b` (RPM, TPM, RPD) and whether anyone has paid credits | Marco (console.groq.com/settings/limits) |
| 10 | Does Groq return `logprobs` for gpt-oss? Needed only for the proposal's log-prob Jenga (§4.4), which is out of scope tonight | Groq docs |
| 11 | II-Search-4B tool calling through Ollama with the GGUF build; is any hosted endpoint with credits offered by Intelligent Internet? | Intelligent Internet rep |
| 12 | Any Intelligent Internet requirement to use their models/stack for partner prizes | Intelligent Internet rep |
| 13 | Event's official name and URL (we're using "OxAI Making Sense Hackathon" from the PDF's file name) | Organisers |
| 14 | Should the AgentSync workspace (Prompt 2) be set up at all with under 5 h left? Go only if a hub already exists and setup ends by 20:00 | Vincent |
