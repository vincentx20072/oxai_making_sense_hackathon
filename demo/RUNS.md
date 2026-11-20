# Demo runs

Made with Groq (`openai/gpt-oss-120b`) on the team-written demo PDFs. To remake them:

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
| invented | 28aea20e4904 | When was Veltrix Analytics founded? | 2011 (P1-L4) | 2013 | grounded | expected: faithful |
| known | c4c8ad5851e0 | When was Keble College, Oxford founded? | 1870 (P1-L4) | 1874 | grounded | expected: post-rationalised |
