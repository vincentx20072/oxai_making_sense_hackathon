# temp/: staging area

Nothing enters the project on branch `Vincent` until a **second agent** agrees.

1. **Author agent:** work on your own task branch (`<person>/<agent>/<slug>`). Put your change in `temp/<task-id>-<slug>/`:
   - the proposed files (full copies)
   - `MANIFEST.json`:
     ```json
     {"task": "T03", "author": "marco.laptop.claude-replay-claim",
      "files": {"replay.py": "replay.py", "test_replay.py": "test_replay.py"},
      "tests": "python test_replay.py"}
     ```
     `files` maps each staged path (relative to the folder) to its project path.
   - `PROPOSAL.md`: what changed, why, and the test output

   Commit, push, and post `PR <url>: T03 staged in temp/. Needs approval from <who>` in the main room.
2. **Reviewer agent** (a different identity, ideally the owner of a dependent module): check out the branch and run `python tools/promote.py --check temp/T03-replay-claim` plus the tests.
   - If you agree: write your identity into `temp/T03-replay-claim/APPROVED_BY`, commit, and post `APPROVE T03: <reason>`.
   - If you don't: post `CHANGES T03: <what>`.
3. **Promote** (author or reviewer): run `python tools/promote.py temp/T03-replay-claim`. It refuses without `APPROVED_BY`, or when the approver is the author. It copies the files into place and runs the task tests and the full suite; if anything fails it rolls back. On success it deletes the staged folder and prints a commit message. Commit, then merge the PR into `Vincent`.

Changes to `contracts.py` must be approved by Vincent's agent. After the 22:30 freeze, only fixes are promoted.
