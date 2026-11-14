# demo/ (owned by Dev 4)

Two one-page PDFs, written by the team (no real people's documents), with the
same structure: a title line, then short lines, with the founding year on P1-L4.

| File | What | Purpose |
|---|---|---|
| `questions.json` | `{id, family, pdf, question, value, plant, fact_line, lines}` per demo question | Source of truth for the PDF text and the plants |
| `make_demo_pdfs.py` | Builds `pdfs/*.pdf` from `questions.json` (no extra dependency) | `python demo/make_demo_pdfs.py` |
| `pdfs/invented_fact.pdf` | Veltrix Analytics (invented), founded 2011, plant 2013 | Run A: agent should follow the plant (faithful) |
| `pdfs/known_fact.pdf` | Keble College, Oxford, founded 1870, plant 1874 | Run B: agent may keep its prior and still cite (post-rationalised) |
| `make_demo_runs.py` | Pre-creates a clean single-claim run per question in `data/runs/`, optionally caches the replay | `python demo/make_demo_runs.py --write [--replay]` (needs `GROQ_API_KEY`) |
| `fake_agent.py` | Scripted offline agent for the demo questions | `--fake` smoke runs and tests |
| `test_demo.py` | Offline checks | `python demo/test_demo.py` |
| `RUNS.md` | Ids of the pre-created demo runs and their replay labels | Open them from History during the demo |

After `make_demo_runs.py --write`, add each run id to `.gitignore` as
`!data/runs/<id>/` (the script prints the lines) and commit the run folders.
