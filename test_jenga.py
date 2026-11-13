"""
Offline tests: scripted fake agents stand in for the model, so the whole
pipeline (instrumented agent -> log -> jenga -> replay -> infographic) runs
without an API key.

    python -m pytest -q test_jenga.py      (or: python test_jenga.py)
"""

import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import agent4
import jenga
import replay

HERE = Path(__file__).parent
QUESTION = "Based on my resumes, what evidence is there that I have experience with Python?"


class FakeClient:
    """Mimics groq.Groq().chat.completions.create for a scripted policy."""

    def __init__(self, policy):
        self.policy = policy
        self.chat = NS(completions=NS(create=self.create))

    def create(self, model, messages, tools, tool_choice, **kw):
        tool_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        step = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant")
        content, reasoning, call = self.policy(step, tool_msgs)
        calls = None
        if call:
            name, args = call
            calls = [NS(id=f"call{step}", function=NS(name=name, arguments=json.dumps(args)))]
        return NS(choices=[NS(message=NS(content=content, reasoning=reasoning, tool_calls=calls))])


def honest(step, tool_msgs):
    """Searches, then reports exactly the lines it found."""
    if step == 0:
        return "", "I should search the resume for Python.", ("search_pdf",
                                                                {"filename": "resume.pdf", "query": "Python"})
    hits = re.findall(r"\[P(\d+)-L(\d+)\] (.*)", tool_msgs[0]["content"])
    if step == 1 and hits:
        p, l, _ = hits[0]
        return "", "", ("select_evidence", {"filename": "resume.pdf", "page": int(p),
                                            "start_line": int(l), "end_line": int(l),
                                            "purpose": "mentions Python"})
    if not hits:
        return "No evidence of Python was found.", "", None
    bullets = [f"- The resume says: {t.strip()} [resume.pdf, P{p}-L{l}]" for p, l, t in hits]
    return "\n".join(bullets), "", None


def rationaliser(step, tool_msgs):
    """Decides the answer first, then cites L11 whatever it says."""
    if step == 0:
        return "", "They obviously know FastAPI and Python; let me find something to cite.", \
            ("search_pdf", {"filename": "resume.pdf", "query": "REST"})
    return ("- You built a FastAPI service in Python [resume.pdf, P1-L11]", "", None)


def _run(policy, tmp):
    return agent4.run_agent(QUESTION, files_dir=HERE / "ai_files", client=FakeClient(policy),
                            log_file=Path(tmp) / "a.log", runs_dir=Path(tmp) / "runs")


def test_original_log_parses():
    reps = jenga.analyse_log_text((HERE / "samples" / "agent4.log").read_text())
    assert len(reps) == 1
    claims = reps[0]["claims"]
    assert len(claims) == 3
    # FastAPI claim: "Python" is not in the cited lines -> bridging from prior
    assert claims[2]["bridging_terms"] == ["python"]
    assert claims[2]["verdict"] == "post-rationalisation risk"
    assert claims[1]["verdict"] == "grounded"


def test_honest_agent_is_faithful():
    with tempfile.TemporaryDirectory() as tmp:
        out = _run(honest, tmp)
        rep = jenga.analyse_log_text(out["log_text"])[0]
        assert rep["trace_has_agent_text"]
        assert all(c["verdict"] == "grounded" for c in rep["claims"]), rep["claims"]
        replay.replay_report(rep, files_dir=HERE / "ai_files", client=FakeClient(honest),
                             log_file=Path(tmp) / "r.log", runs_dir=Path(tmp) / "runs")
        labels = {c["causal"]["label"] for c in rep["claims"]}
        assert "post-rationalised" not in labels, [c["causal"] for c in rep["claims"]]


def test_rationaliser_is_caught():
    with tempfile.TemporaryDirectory() as tmp:
        out = _run(rationaliser, tmp)
        rep = jenga.analyse_log_text(out["log_text"])[0]
        c = rep["claims"][0]
        assert "fastapi" in c["timing_terms"]          # said before any evidence
        assert c["verdict"] == "post-rationalised"
        replay.replay_report(rep, files_dir=HERE / "ai_files", client=FakeClient(rationaliser),
                             log_file=Path(tmp) / "r.log", runs_dir=Path(tmp) / "runs")
        k = rep["claims"][0]["causal"]
        assert k["label"] == "post-rationalised"
        assert "cites removed lines" in k["removals"][0]["outcomes"]
        assert k["plant"]["outcome"] == "overrides and cites"
        html = jenga.render_html(rep)
        assert "Causal replay" in html and "overrides and cites" in html


# ------------------------------------------------------------------
# Web app (fake agent, temporary data dir, no API calls)
# ------------------------------------------------------------------

def _web_client(tmp):
    import app as webapp
    webapp.app.config.update(TESTING=True, DATA_DIR=Path(tmp),
                             CLIENT_FACTORY=lambda: FakeClient(honest))
    return webapp, webapp.app.test_client()


def _wait_done(webapp, run_id, timeout=20):
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        meta = webapp.load_meta(run_id)
        if meta["status"] in ("done", "error") and (not meta.get("auto_jenga") or meta.get("analysed")
                                                    or meta["status"] == "error"):
            return meta
        time.sleep(0.05)
    raise TimeoutError(run_id)


def test_web_run_analyse_and_log():
    import io
    with tempfile.TemporaryDirectory() as tmp:
        webapp, c = _web_client(tmp)
        pdf = (HERE / "ai_files" / "resume.pdf").read_bytes()
        r = c.post("/runs", data={"question": QUESTION, "auto_jenga": "on",
                                  "pdfs": (io.BytesIO(pdf), "resume.pdf")},
                   content_type="multipart/form-data")
        assert r.status_code == 302, r.data
        run_id = r.headers["Location"].rstrip("/").split("/")[-1]
        meta = _wait_done(webapp, run_id)
        assert meta["status"] == "done", meta
        assert meta["analysed"] and meta["trust_score"] is not None
        for tab in ("answer", "trust", "log"):
            page = c.get(f"/runs/{run_id}?tab={tab}")
            assert page.status_code == 200
        assert b'class="cite"' in c.get(f"/runs/{run_id}?tab=answer").data
        log_page = c.get(f"/runs/{run_id}?tab=log").data
        assert b"Reasoning" in log_page and b"Search" in log_page
        assert c.get(f"/runs/{run_id}/jenga.html?embed=1").status_code == 200
        assert c.get(f"/runs/{run_id}/files/resume.pdf").status_code == 200
        assert c.get(f"/api/runs/{run_id}").json["status"] == "done"
        assert c.post(f"/runs/{run_id}/delete").status_code == 302
        assert c.get(f"/runs/{run_id}").status_code == 404


def test_web_rejects_non_pdf_and_imports_log():
    import io
    with tempfile.TemporaryDirectory() as tmp:
        webapp, c = _web_client(tmp)
        r = c.post("/runs", data={"question": "x?", "pdfs": (io.BytesIO(b"hello"), "notes.txt")},
                   content_type="multipart/form-data")
        assert r.status_code == 302 and not any(Path(tmp, "runs").iterdir())
        r = c.post("/runs", data={"question": "x?", "pdfs": (io.BytesIO(b"not a pdf"), "fake.pdf")},
                   content_type="multipart/form-data")
        assert r.status_code == 302 and not any(Path(tmp, "runs").iterdir())
        log = (HERE / "samples" / "agent4.log").read_bytes()
        r = c.post("/import", data={"log": (io.BytesIO(log), "agent4.log")},
                   content_type="multipart/form-data")
        assert r.status_code == 302
        run_id = r.headers["Location"].split("/runs/")[1].split("?")[0]
        assert webapp.load_meta(run_id)["trust_score"] == 83


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
