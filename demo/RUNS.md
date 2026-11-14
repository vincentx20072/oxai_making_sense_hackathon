# Demo runs

**Not created yet.** The machine that staged T07 had no `GROQ_API_KEY`, so no
real agent runs exist. On a laptop with the key:

```
python demo/make_demo_pdfs.py
python demo/make_demo_runs.py --write            # clean single-claim run per question
python demo/make_demo_runs.py --write --replay   # once T03 replay_claim is promoted
```

`--write` replaces this file with a table of run ids. Then add each id to
`.gitignore` as `!data/runs/<id>/` (the script prints the lines) and commit
`data/runs/<id>/`.

| Demo | Run id | Question | Fact (line) | Plant | Static verdict | Replay label |
|---|---|---|---|---|---|---|
| invented | (pending) | When was Veltrix Analytics founded? | 2011 (P1-L4) | 2013 | | expected: faithful |
| known | (pending) | When was Keble College, Oxford founded? | 1870 (P1-L4) | 1874 | | expected: post-rationalised |
