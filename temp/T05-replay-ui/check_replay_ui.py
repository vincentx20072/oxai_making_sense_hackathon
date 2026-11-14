"""T05 check: Pull-the-block routes + Trust tab, offline (no API calls).

    python check_replay_ui.py <repo root>
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
import app as webapp  # noqa: E402
import replay  # noqa: E402

RUN = "81518514ddfd"


def wait(c, claim, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get(f"/api/runs/{RUN}/replay/{claim}").json
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise TimeoutError(claim)


with tempfile.TemporaryDirectory() as tmp:
    shutil.copytree(ROOT / "data" / "runs" / RUN, Path(tmp) / "runs" / RUN)
    webapp.app.config.update(TESTING=True, DATA_DIR=Path(tmp))
    c = webapp.app.test_client()

    page = c.get(f"/runs/{RUN}?tab=trust").data.decode()
    assert page.count('class="btn small pull-btn"') == 5, "5 cited claims get a button"
    assert "Cites no lines" in page                      # c6 is uncited
    assert "Reruns the agent 2×: about 2 API calls (cap 12)" in page
    assert 'initReplay("81518514ddfd")' in page
    print("ok trust tab renders buttons + cost estimate")

    # validation
    assert c.post(f"/runs/{RUN}/replay", json={}).status_code == 400
    assert c.post(f"/runs/{RUN}/replay", json={"claim_id": "zz"}).status_code == 404
    assert c.post(f"/runs/{RUN}/replay", json={"claim_id": "c6"}).status_code == 400
    assert c.get(f"/api/runs/{RUN}/replay/c1").status_code == 404
    print("ok validation")

    # 1) the T01 stub replay engine
    r = c.post(f"/runs/{RUN}/replay", json={"claim_id": "c1"})
    assert r.status_code == 202 and r.json["status"] == "running" and r.json["calls_estimated"] == 2, r.json
    s = wait(c, "c1")
    assert s["status"] == "done" and s["causal"]["label"] == "unclear", s
    r = c.post(f"/runs/{RUN}/replay", json={"claim_id": "c1"})
    assert r.status_code == 200 and r.json["cached"] and r.json["calls_used"] == 0
    print("ok stub replay -> done -> cached")

    # 2) a realistic result, as T03 will return it
    calls = []

    def fake_replay_claim(run_id, claim_id, **kw):
        calls.append(claim_id)
        time.sleep(0.3)
        return {"run_id": run_id, "claim_id": claim_id, "status": "done", "cached": False,
                "calls_used": 2, "calls_estimated": 2,
                "before_answer": "Proficient in Java, Python, C/C++ [resume.pdf, P1-L12]",
                "after_answers": {"remove_all": "Proficient in Java and Python [resume.pdf, P1-L12]",
                                  "plant": "Proficient in Java, Python, C/C++ [resume.pdf, P1-L12]"},
                "causal": {"label": "post-rationalised", "E": 0.0, "F": 0.0,
                           "removals": [{"span": "all", "outcomes": ["cites removed lines"],
                                         "survived": True, "new_cites": []}],
                           "remove_all_outcomes": ["cites removed lines"],
                           "plant": {"original": "C/C++", "planted": "Rust", "magnitude": "small",
                                     "outcome": "overrides and cites", "outcomes": ["overrides and cites"]},
                           "samples": 1},
                "error": None}

    replay.replay_claim = fake_replay_claim
    r = c.post(f"/runs/{RUN}/replay", json={"claim_id": "c2"})
    assert r.status_code == 202
    r2 = c.post(f"/runs/{RUN}/replay", json={"claim_id": "c2"})   # double click while running
    assert r2.status_code == 202 and r2.json["status"] == "running"
    page = c.get(f"/runs/{RUN}?tab=trust").data.decode()
    assert 'data-claim="c2" data-state="running"' in page
    s = wait(c, "c2")
    assert s["causal"]["label"] == "post-rationalised" and calls == ["c2"], (s, calls)
    r = c.post(f"/runs/{RUN}/replay", json={"claim_id": "c2"})
    assert r.json["cached"] and r.json["calls_used"] == 0 and calls == ["c2"]
    page = c.get(f"/runs/{RUN}?tab=trust").data.decode()
    assert 'data-claim="c2" data-state="done"' in page and "Cached: 0 API calls" in page
    print("ok fake replay: running -> done, one job on double click, cached on reload")

    # 3) a failing engine does not poison the cache, and can be retried
    def boom(*a, **kw):
        raise RuntimeError("rate limited")
    replay.replay_claim = boom
    c.post(f"/runs/{RUN}/replay", json={"claim_id": "c3"})
    s = wait(c, "c3")
    assert s["status"] == "error" and "rate limited" in s["error"], s
    assert not (Path(tmp) / "runs" / RUN / "replay" / "c3.json").exists()
    replay.replay_claim = fake_replay_claim
    assert c.post(f"/runs/{RUN}/replay", json={"claim_id": "c3"}).status_code == 202
    assert wait(c, "c3")["status"] == "done"
    print("ok error -> retry")

    # 4) a 'running' job left by a restarted server is reported as interrupted
    webapp._write_json(webapp.replay_job_path(RUN, "c4"),
                       {"run_id": RUN, "claim_id": "c4", "status": "running"})
    s = c.get(f"/api/runs/{RUN}/replay/c4").json
    assert s["status"] == "error" and "interrupted" in s["error"]
    print("ok stale job")

print("ALL OK")
