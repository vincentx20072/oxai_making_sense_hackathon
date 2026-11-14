# T10: claim alibi field + Alibi x Jenga 2x2 in the report

## What
- `jenga.analyse_claim` now sets `claim["alibi"]` (supported | unsupported | phantom), the
  `Alibi` field T01 already put in `contracts.py` (`NotRequired[Alibi]`). **No contract change.**
  - **phantom**: a cited line was never shown to the agent (same test as the phantom-citation verdict).
  - **supported**: cited, all lines shown, and the citation carries the claim (no bridging term,
    no term found in no source, effective evidence share >= 0.5). This is the verdict logic minus timing.
  - **unsupported**: everything else (uncited, or a citation that doesn't carry the claim).
- `jenga_use(claim)` gives the Jenga axis: `used` / `not used`, from the replay label when it is
  `faithful` / `post-rationalised`, otherwise from the static timing signal. It also returns the source.
- `alibi_2x2(claims)` buckets claims; `render_html` adds an **Alibi × Jenga** table after the claim
  cards (Faithful citation / Cited but not used / Used but mis-cited / Unsupported and unused), with
  claim ids per cell and a note saying which signal drove the Jenga axis. Phantom claims sit on the
  unsupported row and are counted in the row header.
- Each claim card gets an alibi chip; `jenga.jsonl` gets an `alibi` field; the CLI prints it.
- No scores or verdicts change (sample log is still 83).

## Why
PLAN §5 / T10: "Alibi asks was it there?, Jenga asks did it use it?". The 2x2 puts the headline
case (supported but not used = cited but not used) on the page.

## Tests
- honest agent: every claim `alibi == "supported"`, all in (supported, used).
- rationaliser: `alibi == "unsupported"` ("python" is in no source), Jenga `not used` (static, then replay);
  the 2x2 renders in `jenga.html`.
- new `test_alibi_phantom_and_cited_but_unused`: a fabricating agent citing an unseen line gets
  `phantom`; a supported claim with a timing flag lands in "Cited but not used".

```
ok test_original_log_parses
ok test_honest_agent_is_faithful
ok test_rationaliser_is_caught
ok test_alibi_phantom_and_cited_but_unused
ok test_web_run_analyse_and_log
ok test_web_rejects_non_pdf_and_imports_log
```
Full suite (test_jenga, test_replay, eval/test_eval, promote --self-test) passes.

## Open issues
- Built on T01's contract (already promoted to Vincent). No new key on JengaReport: the 2x2 is
  computed at render time, to avoid a contract change.
- "Used but mis-cited" is driven by the static timing signal, so without replays it means only "no
  early assertion". That is a weak proxy, and the table says so.
