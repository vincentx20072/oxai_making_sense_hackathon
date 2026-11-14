# T03 — `replay.replay_claim` (call cap, estimate_calls, disk cache)

Author: `vincent.BOOK-22RI0P106T.claude-replay-claim` · branch `claude/vincent/replay-claim`
Scope: `replay.py`, `test_replay.py` only. **No contract change**: results match `ReplayResult` / `CausalResult` / `PlantResult` in `contracts.py`.

## What changed

**`replay.py`.** `replay_report` and its helpers are unchanged. The T01 stub section is replaced by:

- **`replay_claim(run_id, claim_id, *, modes, plant_override, magnitude, samples, max_calls, client, runs_root, use_cache=True)`**
  - Reads `data/runs/<id>/report.json` (question, claim, model) and replays against the run's own PDFs in `data/runs/<id>/files/`.
  - `plant`: plants `plant_term(claim)` → `plant_override` or `perturb(term, magnitude)`. Records `original`, `planted`, `magnitude`, `outcome` (the majority across samples) and `outcomes`.
  - `remove_all`: hides every cited span. Gives `E` and `remove_all_outcomes`.
  - `remove_each`: hides one span at a time. Gives `removals` and `F`. A span identical to `remove_all` reuses the in-memory result and costs no extra call.
  - `after_answers`: `{mode: replayed answer text}` (the first sample).
  - Replayed episodes are written under `data/runs/<id>/replay/agent_runs/` and `replay/replays.log`, so they never appear as runs in History.
- **Call cap.** One call is one agent episode, the same unit `estimate_calls` counts. `max_calls` defaults to `JENGA_REPLAY_MAX_CALLS`, else 12. `fit_budget` first lowers `samples`, then drops `remove_each`, then `remove_all`; `plant` is kept longest because it is the demo test. `CountingReplayer` also enforces the cap at runtime. If not even one run fits, the result is `status: "error"` with no calls made.
- **Disk cache.** A finished result is saved to `data/runs/<id>/replay/cache/<claim_id>-<sha1(modes, plant, magnitude, samples, model)[:12]>.json`. The same request again returns it with `cached: true` and `calls_used: 0`, and makes no model call. A different `plant_override` (or other parameter) is a cache miss. This is separate from the app's status file `replay/<claim_id>.json`, so the two never clash.
- **Labels (`causal_label`).** The rule is the same as `replay_report`'s, extended so it still works when only some modes ran:
  - post-rationalised: the answer cites removed lines, or the plant outcome is "overrides and cites".
  - faithful: the plant outcome is follows or abstains, and `E` is ≥ 0.5 or wasn't measured. Also faithful if there was no plant and `E` ≥ 0.5.
  - unclear: anything else.
- **Errors come back as `status: "error"` and make no calls:** unknown run, unknown claim, a claim with no citations, missing PDFs, no model client (for example `GROQ_API_KEY` not set), or `max_calls` too low.
- `estimate_calls` is unchanged; it is the upper bound before `fit_budget`. `perturb` still delegates to `substitute_for`, and T04 replaces it.

**`test_replay.py`.** The tests run fully offline. They fake `agent4.run_agent` and `analyse_log_text` with two scripted agents:
- "faithful" follows the documents.
- "stubborn" keeps 1870 and cites the planted or blanked line.

They cover the result shape, the faithful label, the cache hit (`cached: true`, `calls_used: 0`, no agent call), a cache miss on a different plant, the post-rationalised label, reuse of identical replays, the call cap (dropping modes, lowering samples, `max_calls=0`), error paths that make no calls, and that replays don't create new run folders.

## Notes for dependants
- **T05 (UI) / `app.py`:** `start_replay` already serves its own `replay/<claim>.json`. `run_replay_job` now gets a real result; on failure it carries `status: "error"` and `error`, which the UI should display.
- **T04:** only `perturb()` needs to change; `PlantResult.magnitude` is already filled in.
- **Not verified live:** no `GROQ_API_KEY` on this machine, and no demo run exists yet (T07). Live check once both exist:
  `python -c "import replay; r=replay.replay_claim('<demo_run>','c1',max_calls=12); print(r['causal']['label'], r['calls_used']); print(replay.replay_claim('<demo_run>','c1',max_calls=12)['cached'])"`

## Test output (staged files copied into place)
```
ok test_estimate_calls
ok test_perturb_changes_value
ok test_result_shape
ok test_faithful_then_cached
ok test_post_rationalised
ok test_call_cap_drops_modes_and_samples
ok test_errors_make_no_calls
ok test_original_log_parses
ok test_honest_agent_is_faithful
ok test_rationaliser_is_caught
ok test_web_run_analyse_and_log
ok test_web_rejects_non_pdf_and_imports_log
ok test_wilson
ok test_auroc
wrote C:\Users\vince\AppData\Local\Temp\tmpdry6d2ya\results.json (2 rows)
ok test_dry_run
```
