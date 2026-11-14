# T06: /results page (table + bar chart with 95% CI)

## What
- **app.py**: `load_results(path)` reads `eval/results.json` (`EvalSummary` from contracts.py) and returns `(summary, groups, error)`. `groups` is `All` + each family (sorted) as `{name, k, n, rate, lo, hi}`, with values clamped to [0, 1]. If `rate` is missing it is computed from k/n. A missing file gives the empty state. Invalid JSON or a non-object gives an error banner instead of a 500. Null or missing keys render as `n/a`, so a partial real run still renders. The new config key `RESULTS_PATH` (env `JENGA_RESULTS_PATH`) overrides the path; it defaults to `eval/results.json`.
- **templates/results.html**: meta chips (model, n, dry-run warning, AUROC). The chart is a horizontal bar per group with a 95% Wilson CI whisker on a 0–100% axis, in pure HTML/CSS (no JS or CDN). Below it are the group table (k/n, rate, CI) and a per-question table (static score, plant and remove-all outcomes, evidence-insensitive, replay label chip).
- **templates/base.html**: a "Pilot results" sidebar link, highlighted when `active == "results"`.
- **static/app.css**: `.ci-*` chart and `.tbl` table styles using the existing theme tokens, so light and dark both work. There is a narrow-screen rule, and wide tables scroll inside their card.

## Why
PLAN §4 step 6 / §8: `/results` shows the pilot (H1 rate + CI by family, H3 AUROC). It builds against the T01 `EvalSummary` contract and needs no contract change.

## Tests
`python temp/T06-results-page/check_results.py` (Flask test client, no network) checks six cases:
1. the dry-run output from `python eval/run_eval.py --dry-run`
2. a synthetic 12-question real-run summary (`dry_run: false`, AUROC 0.72)
3. a partial file with nulls and missing keys
4. invalid JSON
5. a missing file
6. the sidebar link

```
results page checks: 6/6 passed
python test_jenga.py && python test_replay.py && python eval/test_eval.py && python tools/promote.py --self-test  -> all OK
```
Note: `check_results.py` lives only in this staging folder (not in `files`), so promote deletes it after the tests run.
