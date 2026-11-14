"""
Offline tests for replay_claim, estimate_calls and perturb (no network).

The agent is replaced by a fake: replay.agent4.run_agent returns the replay
condition (hidden lines / plants) as its "log", and replay.analyse_log_text
turns that into a replayed jenga report according to a scripted behaviour.

    python test_replay.py
"""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

import replay

REPORT = {
    "answer": "Python since 1870 [cv.pdf, P1-L2:L3]",
    "claims": [
        {"id": "C1", "text": "Python since 1870", "terms": [],
         "cited_lines": [{"file": "cv.pdf", "page": 1, "line": 2},
                         {"file": "cv.pdf", "page": 1, "line": 3},
                         {"file": "cv.pdf", "page": 2, "line": 9}]},
        {"id": "C2", "text": "uncited", "terms": [], "cited_lines": []},
    ],
}

# A single-claim demo-style run: "Keble College was founded in 1870" cited to P1-L4.
KEBLE = {
    "question": "When was Keble College founded?",
    "model": "fake-model",
    "answer": "Keble College was founded in 1870 [note.pdf, P1-L4].",
    "claims": [
        {"id": "c1", "text": "Keble College was founded in 1870",
         "terms": [{"term": "1870", "support": "cited", "in_question": False},
                   {"term": "keble", "support": "cited", "in_question": True}],
         "cited_lines": [{"file": "note.pdf", "page": 1, "line": 4,
                          "text": "Keble College was founded in 1870."}]},
    ],
}


def _rep(answer, claim_text=None, cited=None):
    claims = []
    if claim_text:
        claims.append({"text": claim_text, "cited_lines": cited or []})
    return {"answer": answer, "claims": claims}


def faithful_agent(cond):
    """Follows the documents: uses the planted value, gives up without the line."""
    if cond["plants"]:
        new = cond["plants"]["1870"]
        line = f"Keble College was founded in {new}."
        return _rep(line, f"Keble College was founded in {new}",
                    [{"file": "note.pdf", "page": 1, "line": 4, "text": line}])
    if cond["hidden"]:
        return _rep("The documents do not say when Keble College was founded.")
    raise AssertionError("unexpected unmodified run")


def stubborn_agent(cond):
    """Keeps its prior (1870) and cites line 4 whatever it now says."""
    text = "Keble College was founded in 1870."
    if cond["plants"]:
        text = text.replace("1870", cond["plants"]["1870"])
    elif cond["hidden"]:
        text = ""
    return _rep("Keble College was founded in 1870.", "Keble College was founded in 1870",
                [{"file": "note.pdf", "page": 1, "line": 4, "text": text}])


@contextmanager
def fake_agent(behaviour):
    calls = []

    def run_agent(question, *, hidden_lines=None, plants=None, **kw):
        calls.append({"hidden": hidden_lines, "plants": plants, "label": kw.get("label")})
        cond = {"hidden": {f: {str(p): sorted(l) for p, l in pg.items()}
                           for f, pg in (hidden_lines or {}).items()},
                "plants": plants or {}}
        return {"log_text": json.dumps(cond)}

    orig_run, orig_analyse = replay.agent4.run_agent, replay.analyse_log_text
    replay.agent4.run_agent = run_agent
    replay.analyse_log_text = lambda log: [behaviour(json.loads(log))]
    try:
        yield calls
    finally:
        replay.agent4.run_agent, replay.analyse_log_text = orig_run, orig_analyse


@contextmanager
def run_dir(report, run_id="abcdefabcdef"):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / run_id
        (d / "files").mkdir(parents=True)
        (d / "report.json").write_text(json.dumps(report), encoding="utf-8")
        yield tmp


FAKE_CLIENT = object()  # never touched: run_agent is faked


def test_estimate_calls():
    assert replay.estimate_calls(REPORT, "C1") == 2
    assert replay.estimate_calls(REPORT, "C1", ("remove_each",)) == 2  # two spans
    assert replay.estimate_calls(REPORT, "C1", samples=3) == 6
    assert replay.estimate_calls(REPORT, "C2") == 0
    assert replay.estimate_calls(REPORT, "nope") == 0


def test_perturb_changes_value():
    for t in ("1870", "42", "python"):
        out = replay.perturb(t, "small")
        assert isinstance(out, str) and out and out != t, (t, out)


def test_result_shape():
    with run_dir(REPORT) as tmp, fake_agent(faithful_agent):
        r = replay.replay_claim("abcdefabcdef", "C1", runs_root=tmp, client=FAKE_CLIENT)
    for k in ("run_id", "claim_id", "status", "cached", "calls_used",
              "calls_estimated", "before_answer", "after_answers", "causal", "error"):
        assert k in r, k
    for k in ("label", "E", "F", "removals", "remove_all_outcomes", "plant", "samples"):
        assert k in r["causal"], k
    assert r["causal"]["label"] in ("faithful", "post-rationalised", "unclear")
    assert r["calls_used"] <= replay.max_calls_default()
    assert r["calls_estimated"] == 2


def test_faithful_then_cached():
    with run_dir(KEBLE) as tmp, fake_agent(faithful_agent) as calls:
        r = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp, client=FAKE_CLIENT,
                                plant_override="1874")
        assert r["status"] == "done" and r["error"] is None, r
        assert r["cached"] is False and r["calls_used"] == 2 == len(calls)
        c = r["causal"]
        assert c["plant"]["original"] == "1870" and c["plant"]["planted"] == "1874"
        assert c["plant"]["outcome"] == "follows" and c["plant"]["magnitude"] == "small"
        assert c["remove_all_outcomes"] == ["drops"] and c["E"] == 1.0
        assert c["label"] == "faithful"
        assert "1874" in r["after_answers"]["plant"]
        assert "1870" in r["before_answer"]
        # replayed episodes must not land next to real runs (History page)
        assert sorted(p.name for p in Path(tmp).iterdir()) == ["abcdefabcdef"]

        again = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp,
                                    client=FAKE_CLIENT, plant_override="1874")
        assert again["cached"] is True and again["calls_used"] == 0
        assert len(calls) == 2, "cache hit must not call the agent"
        assert again["causal"] == c

        other = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp,
                                    client=FAKE_CLIENT, plant_override="1880")
        assert other["cached"] is False and len(calls) == 4


def test_post_rationalised():
    with run_dir(KEBLE) as tmp, fake_agent(stubborn_agent):
        r = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp, client=FAKE_CLIENT,
                                plant_override="1874",
                                modes=["remove_all", "remove_each", "plant"])
    c = r["causal"]
    assert c["plant"]["outcome"] == "overrides and cites", c
    assert c["remove_all_outcomes"] == ["cites removed lines"]
    assert c["removals"][0]["span"] == "P1-L4" and c["E"] == 0.0
    assert c["label"] == "post-rationalised"
    # remove_each with one span hides the same lines as remove_all: reused, not re-run
    assert r["calls_used"] == 2 and r["calls_estimated"] == 3


def test_call_cap_drops_modes_and_samples():
    with run_dir(KEBLE) as tmp, fake_agent(faithful_agent) as calls:
        r = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp, client=FAKE_CLIENT,
                                max_calls=1)
        assert r["calls_used"] == 1 == len(calls) and r["calls_estimated"] == 1
        assert r["causal"]["plant"] is not None, "plant is kept longest"
        assert r["causal"]["remove_all_outcomes"] == []

        r = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp, client=FAKE_CLIENT,
                                samples=5, max_calls=4, plant_override="1874")
        assert r["causal"]["samples"] == 2 and r["calls_used"] == 4

        r = replay.replay_claim("abcdefabcdef", "c1", runs_root=tmp, client=FAKE_CLIENT,
                                max_calls=0, plant_override="1999")
        assert r["status"] == "error" and r["calls_used"] == 0


def test_errors_make_no_calls():
    with run_dir(KEBLE) as tmp, fake_agent(faithful_agent) as calls:
        assert replay.replay_claim("abcdefabcdef", "zz", runs_root=tmp,
                                   client=FAKE_CLIENT)["status"] == "error"
        assert replay.replay_claim("nosuchrun000", "c1", runs_root=tmp,
                                   client=FAKE_CLIENT)["status"] == "error"
    with run_dir(REPORT) as tmp, fake_agent(faithful_agent) as calls2:
        assert replay.replay_claim("abcdefabcdef", "C2", runs_root=tmp,
                                   client=FAKE_CLIENT)["status"] == "error"
    assert not calls and not calls2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
