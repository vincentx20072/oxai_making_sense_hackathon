# T07: demo dataset (PDFs, questions, run tooling)

Author: `vincent.BOOK-22RI0P106T.claude-demo-dataset`. Scope: `demo/` only.

## What

| Project path | Change |
|---|---|
| `demo/questions.json` | Two demo questions, `{id, family, pdf, question, value, plant, fact_line, lines}`. The PDF text lives here too |
| `demo/make_demo_pdfs.py` | Builds `demo/pdfs/*.pdf` from `questions.json`. A ~60-line PDF writer (Helvetica, one A4 page), so **no new dependency** (fpdf2 was suggested in demo/README but isn't installed, and `requirements.txt` is a hot file) |
| `demo/pdfs/invented_fact.pdf` | Veltrix Analytics (invented): "Veltrix Analytics was founded in 2011." on P1-L4. Plant 2013 |
| `demo/pdfs/known_fact.pdf` | Keble College, Oxford: "Keble College, Oxford was founded in 1870." on P1-L4. Plant 1874 |
| `demo/make_demo_runs.py` | Pre-creates one run per question the same way `POST /runs` does (`app.run_agent_job`, auto Jenga), keeps it only if it is clean (exactly 1 claim, contains the value, cites the fact line), else deletes and retries. `--replay` caches `replay.replay_claim(run, "c1", plant_override=…)`; `--write` writes `demo/RUNS.md`; `--fake` uses a scripted agent |
| `demo/fake_agent.py` | Scripted honest agent for the demo questions (offline smoke runs and tests) |
| `demo/test_demo.py` | Offline checks (below) |
| `demo/RUNS.md` | Placeholder: run ids pending (see Open issues) |
| `demo/README.md` | Describes the folder |

## Why these choices

- Each fact line names the entity ("Keble College, Oxford was founded in 1870."). With "The college was founded in 1870." static Jenga flagged `keble, oxford` as bridged from the prior (score 55, "post-rationalisation risk") even for an honest agent. Now an honest run scores **grounded 100**, so on stage the static score looks fine for both runs and only the replay separates them, which is the pitch.
- Each value appears exactly once in its PDF, so `apply_plants` changes only the cited line. Plants are small (+2 years).
- The questions ask for "one sentence with a citation" to get the single-claim answer the demo needs.

## Tests

`python demo/test_demo.py` (task test):
```
ok  test_fake_runs_clean_and_open_from_history
ok  test_pdfs_round_trip
ok  test_questions_well_formed
ok  test_runs_md_ids_exist
4 passed
```
It checks: questions are consistent (value once, on `fact_line`; plant absent and small); pypdf reads the PDFs back as exactly `lines`, and the committed PDFs match the generator; with the scripted agent, `make_demo_runs` creates runs that are listed on `/` (History), open at `/runs/<id>` (200), have one grounded claim citing P1-L4, and planting the value changes the answer; any run id in `RUNS.md` exists in `data/runs/` and is clean.

Full suite with the files in place: `test_jenga.py`, `test_replay.py`, `eval/test_eval.py`, `tools/promote.py --self-test` all pass.

## Open issues (task not fully done)

1. **No real demo runs yet.** This machine has no `GROQ_API_KEY` (not in the env, user or machine variables), so `data/runs/` has nothing new. Done-when ("both demo runs open from History, single-claim cited answer") needs one command on a laptop with the key: `python demo/make_demo_runs.py --write`. That run, and its `data/runs/<id>/` folders, should go through a small follow-up promotion.
2. **`.gitignore` is outside T07's scope** (`demo/`, `data/runs/`), but each demo run needs `!data/runs/<id>/` there. The script prints the lines; whoever owns `.gitignore` (T01/Vincent) adds them, or the follow-up uses `git add -f data/runs/<id>`.
3. **Replay labels depend on T03.** `replay_claim` is still the T01 stub (always `unclear`, 0 calls), so `--replay` only works after T03 is promoted. Built against the stub's signature in `replay.py` (`plant_override`, `samples`, `max_calls`, `client`, `runs_root`).
4. The existing run `81518514ddfd` contains a real CV (`resume.pdf`, PLAN §3.5). Not touched here; once the demo runs exist it can be dropped from `.gitignore`/`data/runs/`.
