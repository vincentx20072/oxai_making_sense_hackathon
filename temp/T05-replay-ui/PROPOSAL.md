# T05: Trust tab "Pull the block" button, cost estimate, polling, causal panel

Author: `vincent.BOOK-22RI0P106T.claude-replay-ui` · Branch: `claude/vincent/replay-ui` (rebased on `origin/Vincent` @ `ad307d9`)

## What

**Trust tab (`templates/run.html`).** Each claim in "How far can you trust this answer?" now has:
- a **Pull the block** button (or "Show replay" once a cached result exists)
- a cost line: `Reruns the agent N×: about N API calls (cap 12)`, or `Cached: 0 API calls` in green
- a collapsible causal panel under the claim
- for claims with no cited lines, "Cites no lines, so there is no block to pull." and no button; claims over the cap get a note and no button

There is also a one-line footnote stating that replay is a behavioural test, not a view of internal reasoning (PLAN hard rule 3).

**Causal panel (`static/app.js`, `static/app.css`).**
1. Click: the button shows "Replaying…", a spinner, and "Pulling the block… (N agent runs)".
2. The page POSTs `/runs/<id>/replay`, then polls `GET /api/runs/<id>/replay/<claim>` every 1.5 s.
3. The panel shows:
   - the **causal label** (`faithful` green, `post-rationalised` red, `unclear` grey) with a one-line explanation
   - the outcome chips (`cites removed lines`, `follows`, …), the plant shown as `original → planted (magnitude)`, and E/F when present
   - **before/after answers** side by side, with the original value highlighted and the planted value marked in red
   - a footer with calls used, or "From cache: 0 API calls"
4. On failure the panel shows the error and the button reads "Try again".
5. If you reload while a replay is running, the claim resumes polling.

All text is set with `textContent`; no `innerHTML` takes server data. Light and dark use the existing tokens.

**Routes (`app.py`).** These keep T01's URLs and shapes and harden them:
- **Separate job file.** `replay/<claim>.json` now only ever holds a finished `ReplayResult` with `status: "done"`; this is the cache. Progress and errors go to `replay/<claim>.job.json`. As a result, `replay_claim`'s own cache (T03) never reads a half-written `running` record, and a failed job never poisons the cache, so retry works.
- **No double jobs.** An in-process `_replay_active` set means a double click or a second tab never starts a second paid job.
- **Stale jobs.** A `running` job left behind by a server restart is reported as "interrupted, try again".
- **Validation.** Unknown claim → 404. A claim with no cited lines → 400. An estimate over `JENGA_REPLAY_MAX_CALLS` → 400 with the cap in the message. `plant_override` is trimmed to 200 characters.
- **Locking.** Reading state and finishing a job happen under one `RLock`. Without it there was a real race where a just-finished job read as "interrupted"; the check script caught it.
- `view_run` passes `estimates`, `replays` (per-claim status) and `max_replay_calls` to the template.
- The module docstring no longer says replays are unavailable in the web app.

## Dependencies
- Built against T01's `replay.replay_claim` / `estimate_calls` / `max_calls_default` stubs and the `ReplayResult` in `contracts.py`. **T03 is not promoted yet**, so live clicks currently return the stub's `unclear` with no after-answers; the panel says "No replayed answers were recorded". No contract change.
- Expects T03's `replay_claim(run_id, claim_id, modes=, plant_override=, runs_root=)` to return a `ReplayResult`. `after_answers` keys may be `remove_all`, `plant` or `remove_each:<span>`, and the panel labels all three.
- Uses an inline SVG icon so `templates/_icons.html` (outside scope) is untouched.

## Test output
`check_replay_ui.py` (in this folder, not promoted) drives the routes and the Trust tab offline. It runs against a temp copy of the committed demo run `81518514ddfd`, with the stub engine, a fake T03-shaped engine and a failing engine:

```
$ python temp/T05-replay-ui/check_replay_ui.py .
ok trust tab renders buttons + cost estimate
ok validation
ok stub replay -> done -> cached
ok fake replay: running -> done, one job on double click, cached on reload
ok error -> retry
ok stale job
ALL OK
```
(The race fix was confirmed with 6 consecutive passes.)

```
$ python test_jenga.py && python test_replay.py && python eval/test_eval.py && python tools/promote.py --self-test
ok test_original_log_parses / ok test_honest_agent_is_faithful / ok test_rationaliser_is_caught
ok test_web_run_analyse_and_log / ok test_web_rejects_non_pdf_and_imports_log
ok test_estimate_calls / ok test_perturb_changes_value / ok test_replay_claim_stub_shape
ok test_wilson / ok test_auroc / ok test_dry_run / ok promote self-test
```

**Visual check:** headless Chrome screenshots of the Trust tab, with a fake engine:
- in-flight spinners
- finished panels (faithful / post-rationalised / unclear)
- the error + Try again state, in dark and light, desktop and narrow width

The layout held in each.

## Review notes
- Cache hits still show the spinner for at least 500 ms, so the click visibly does something on stage.
- The polling ceiling is about 20 minutes, after which the panel asks the user to reload.
