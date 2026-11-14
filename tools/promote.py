"""
Promote a staged change from temp/ into the project, but only after a second
agent has agreed to it.

    python tools/promote.py temp/T03-replay-claim     # promote one staged task
    python tools/promote.py --check temp/T03-...      # validate only, change nothing
    python tools/promote.py --self-test               # offline self-test

A staged folder looks like:

    temp/T03-replay-claim/
        MANIFEST.json   {"task": "T03", "author": "marco.laptop.claude-replay-claim",
                         "files": {"replay.py": "replay.py"},   # staged path (relative
                                                                 # to the folder) -> project path
                         "tests": "python test_replay.py"}
        PROPOSAL.md     what changed, why, test output
        APPROVED_BY     identity of the reviewing agent (written by the reviewer)
        replay.py       the proposed file

Rules enforced here:
  * APPROVED_BY must exist, be non-empty and differ from MANIFEST["author"].
  * Destinations must stay inside the project; temp/, .git/, .env* and
    .agentsync/ are never valid destinations.
  * After copying, the task's own tests and then the full test command run;
    on any failure every touched file is restored and the folder is kept.
  * On success the staged folder is deleted and a commit message is printed.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL_TESTS = ("python test_jenga.py && python test_replay.py && "
              "python eval/test_eval.py")
FORBIDDEN = ("temp", ".git", ".agentsync")


class PromoteError(Exception):
    pass


def _load(folder):
    folder = Path(folder).resolve()
    man_path = folder / "MANIFEST.json"
    if not man_path.is_file():
        raise PromoteError(f"{folder}: no MANIFEST.json")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    for key in ("task", "author", "files"):
        if not man.get(key):
            raise PromoteError(f"MANIFEST.json: missing '{key}'")
    appr = folder / "APPROVED_BY"
    if not appr.is_file() or not appr.read_text(encoding="utf-8").strip():
        raise PromoteError("not approved: a second agent must write APPROVED_BY "
                           "and post 'APPROVE <task>: <reason>' in the main room")
    approver = appr.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if approver == man["author"]:
        raise PromoteError(f"self-approval: APPROVED_BY ({approver}) is the author")
    return folder, man, approver


def _plan(folder, man, root):
    moves = []
    for src_rel, dst_rel in man["files"].items():
        src = (folder / src_rel).resolve()
        dst = (root / dst_rel).resolve()
        if not src.is_file():
            raise PromoteError(f"staged file missing: {src_rel}")
        if folder not in src.parents:
            raise PromoteError(f"staged path escapes the folder: {src_rel}")
        if root not in dst.parents:
            raise PromoteError(f"destination outside the project: {dst_rel}")
        first = dst.relative_to(root).parts[0]
        if first in FORBIDDEN or dst.name.startswith(".env"):
            raise PromoteError(f"forbidden destination: {dst_rel}")
        moves.append((src, dst))
    return moves


def _run(cmd, root):
    if not cmd:
        return True
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, cwd=root).returncode == 0


def promote(folder, root=ROOT, full_tests=FULL_TESTS, check_only=False):
    root = Path(root).resolve()
    folder, man, approver = _load(folder)
    moves = _plan(folder, man, root)
    if check_only:
        print(f"OK {man['task']}: {len(moves)} file(s), approved by {approver}")
        return True

    backup = {}
    for _, dst in moves:
        backup[dst] = dst.read_bytes() if dst.exists() else None
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    if not (_run(man.get("tests"), root) and _run(full_tests, root)):
        for dst, data in backup.items():
            if data is None:
                dst.unlink(missing_ok=True)
            else:
                dst.write_bytes(data)
        raise PromoteError("tests failed: project restored, staged folder kept")

    shutil.rmtree(folder)
    dests = ", ".join(str(d.relative_to(root)) for _, d in moves)
    print("\nPromoted. Commit with:\n")
    print(f"  git add -A && git commit -m \"{man['task']}: promote from temp/ "
          f"(author {man['author']}, approved by {approver})\" -m \"Files: {dests}\"")
    return True


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "temp").mkdir()
        (root / "mod.py").write_text("X = 1\n")
        stage = root / "temp" / "T99-demo"
        stage.mkdir()
        (stage / "mod.py").write_text("X = 2\n")
        man = {"task": "T99", "author": "a.ci.claude-demo",
               "files": {"mod.py": "mod.py"}, "tests": "python -c \"import mod; assert mod.X == 2\""}
        (stage / "MANIFEST.json").write_text(json.dumps(man))

        def expect_fail(why):
            try:
                promote(stage, root, full_tests="")
            except PromoteError as e:
                assert why in str(e), e
            else:
                raise AssertionError(f"expected failure: {why}")

        expect_fail("not approved")
        (stage / "APPROVED_BY").write_text("a.ci.claude-demo\n")
        expect_fail("self-approval")
        (stage / "APPROVED_BY").write_text("b.ci.claude-review\n")

        man["files"] = {"mod.py": ".env"}
        (stage / "MANIFEST.json").write_text(json.dumps(man))
        expect_fail("forbidden destination")

        man["files"] = {"mod.py": "mod.py"}
        man["tests"] = "python -c \"raise SystemExit(1)\""
        (stage / "MANIFEST.json").write_text(json.dumps(man))
        expect_fail("tests failed")
        assert (root / "mod.py").read_text() == "X = 1\n", "rollback failed"
        assert stage.exists()

        man["tests"] = "python -c \"import mod; assert mod.X == 2\""
        (stage / "MANIFEST.json").write_text(json.dumps(man))
        promote(stage, root, full_tests="")
        assert (root / "mod.py").read_text() == "X = 2\n"
        assert not stage.exists()
    print("ok promote self-test")


def main(argv):
    if argv[:1] == ["--self-test"]:
        self_test()
        return 0
    check = argv[:1] == ["--check"]
    args = argv[1:] if check else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        promote(args[0], check_only=check)
    except PromoteError as e:
        print(f"REFUSED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
