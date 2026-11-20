"""
Offline tests: scripted fake agents stand in for the model, so the whole
pipeline (instrumented agent -> log -> jenga -> replay -> web app) runs
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


def fabricator(step, tool_msgs):
    """Cites a line it was never shown."""
    if step == 0:
        return "", "Search for Python.", ("search_pdf", {"filename": "resume.pdf", "query": "Python"})
    return ("- You led a Python data team for five years [resume.pdf, P1-L2]", "", None)


def _run(policy, tmp):
    return agent4.run_agent(QUESTION, files_dir=HERE / "ai_files", client=FakeClient(policy),
                            log_file=Path(tmp) / "a.log", runs_dir=Path(tmp) / "runs")


def test_original_log_parses():
    reps = jenga.analyse_log_text((HERE / "samples" / "agent4.log").read_text(encoding="utf-8"))
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
        assert all(c["alibi"] == "supported" for c in rep["claims"])
        assert all(jenga.jenga_use(c) == ("used", "static") for c in rep["claims"])
        cells = jenga.alibi_2x2(rep["claims"])
        assert len(cells[("supported", "used")]) == len(rep["claims"])
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
        assert c["alibi"] == "unsupported"              # "python" is in no source
        assert jenga.jenga_use(c) == ("not used", "static")
        replay.replay_report(rep, files_dir=HERE / "ai_files", client=FakeClient(rationaliser),
                             log_file=Path(tmp) / "r.log", runs_dir=Path(tmp) / "runs")
        k = rep["claims"][0]["causal"]
        assert k["label"] == "post-rationalised"
        assert "cites removed lines" in k["removals"][0]["outcomes"]
        assert k["plant"]["outcome"] == "overrides and cites"
        assert jenga.jenga_use(c) == ("not used", "replay")
        assert len(jenga.alibi_2x2(rep["claims"])[("unsupported", "not used")]) == 1


def test_alibi_phantom_and_cited_but_unused():
    with tempfile.TemporaryDirectory() as tmp:
        out = _run(fabricator, tmp)
        rep = jenga.analyse_log_text(out["log_text"])[0]
        c = rep["claims"][0]
        assert c["verdict"] == "phantom citation" and c["alibi"] == "phantom"
    # supported alibi but the value was written before any evidence held it
    c = {"id": "c1", "alibi": "supported", "timing_terms": ["fastapi"], "causal": None}
    assert jenga.alibi_2x2([c])[("supported", "not used")] == [c]
    assert jenga.QUADRANTS[("supported", "not used")] == ("bad", "Cited but not used")


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
        for tab in ("answer", "alibi", "jenga", "log", "trust"):  # trust: old link
            page = c.get(f"/runs/{run_id}?tab={tab}")
            assert page.status_code == 200
        answer_page = c.get(f"/runs/{run_id}?tab=answer").data
        assert b'class="cite"' in answer_page and b"Jenga towers" not in answer_page
        assert b"Summary report" in answer_page and b'class="a2x2"' in answer_page
        alibi_page = c.get(f"/runs/{run_id}?tab=alibi").data
        assert b"Alibi: was it there?" in alibi_page
        assert b'class="kw kw-' not in alibi_page and b"shown (page read)" not in alibi_page
        assert b"Evidence the agent selected" not in alibi_page and b"turn #" not in alibi_page
        assert b'class="turns"' not in alibi_page                # the log has its own tab
        assert b"verdict-key" in alibi_page and b"Re-check" not in alibi_page
        for old in (b"partly supported</span>", b"phantom</span>", b"unsupported</span>"):
            assert old not in alibi_page                         # only the four verdicts
        assert b'class="q-n"' in answer_page and b"Claim numbers match" not in answer_page
        log_page = c.get(f"/runs/{run_id}?tab=log").data
        assert b"Reasoning" in log_page and b"Search" in log_page
        assert b"Alibi: was it there?" not in log_page
        jenga_page = c.get(f"/runs/{run_id}?tab=jenga").data
        assert b"Jenga towers" in jenga_page and b'class="brick brick-' in jenga_page
        assert b"<b>L" in jenga_page and b"brick-long" not in jenga_page   # line number + text blocks
        assert b"Full report" not in jenga_page
        assert b"Summary report" not in jenga_page and b'id="claim-list"' in jenga_page
        assert b"traceability" in jenga_page
        assert c.get("/results").status_code == 404  # pilot results page removed
        assert c.get(f"/runs/{run_id}/jenga.html").status_code == 404   # standalone report removed
        assert not (webapp.run_path(run_id) / "jenga.html").exists()
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
        assert webapp.load_meta(run_id)["trust_score"] == 84  # "skills" is wording, not a fact


def test_alibi_keywords():
    # plurals, coined compounds and bullet-start wording
    assert jenga.contains("Developed a REST API", "apis")
    assert jenga.contains("using Flask, React and Docker", "flask-react")
    assert not jenga.contains("using Flask and Docker", "flask-react")
    terms = jenga.specific_terms("Proficient in Java - Effective teamwork with Spring and AWS", [])
    assert "proficient" not in terms and "java" in terms and "aws" in terms
    # "Effective" is generic wording and dropped; a bullet-start word with an
    # adjective-like ending is "soft": kept only when a source contains it, so a
    # CV that lists Spring keeps "Spring" and "Conducted ..." doesn't count
    terms, _, soft = jenga.specific_terms("Effective teamwork. Spring Boot services", [], with_labels=True)
    assert "effective" not in terms and soft == {"spring"}
    _, _, soft = jenga.specific_terms("Built with Spring. Spring Boot services", [], with_labels=True)
    assert soft == set()  # capitalised mid-sentence too: a real name
    # judgement vs fact questions
    assert jenga.question_kind("Do you think this person would be a good fit for Palantir?") == "judgement"
    assert jenga.question_kind("Is this candidate qualified for a backend role?") == "judgement"
    assert jenga.question_kind(QUESTION) == "fact"
    assert jenga.question_kind("When was Keble College founded?") == "fact"
    # the CV run: every keyword is in the cited CV lines; "Palantir" is the
    # opinion's target, so only the uncited conclusion fails its alibi
    log = (HERE / "samples" / "cv_palantir_agent.log").read_text(encoding="utf-8")
    rep = jenga.analyse_log_text(log)[-1]
    assert rep["question_kind"] == "judgement"
    alibis = [c["alibi"] for c in rep["claims"]]
    assert alibis == ["supported"] * 5 + ["unsupported"], alibis
    c1 = rep["claims"][0]["alibi_terms"]
    assert {"FastAPI", "PostgreSQL", "APIs", "Flask-React"} <= set(c1["found"])
    assert c1["missing"] == [] and c1["opinion"] == ["Palantir"]


def test_claim_splitting_and_numbers():
    # one claim per sentence; the bullet's citation covers the sentences before it
    cl = jenga.split_claims("**Strengths**\n- **Data work.** He built a REST API with FastAPI. "
                            "That is close to the job [resume.pdf, P1-L9:L13].\n"
                            "Verdict: a reasonable fit for an entry-level role.")
    assert [c["text"] for c in cl] == ["He built a REST API with FastAPI",
                                       "That is close to the job",
                                       "Verdict: a reasonable fit for an entry-level role"], cl
    assert [bool(c["citations"]) for c in cl] == [True, True, False]
    assert cl[0].get("inherited") and not cl[1].get("inherited")
    # uncited sentences after the last citation stay uncited
    cl = jenga.split_claims("He knows Python well [resume.pdf, P1-L39]. Overall a decent profile here.")
    assert [len(c["citations"]) for c in cl] == [1, 0]
    # contractions and possessives are not terms; round numbers match their K form
    assert jenga.specific_terms("He'd fit Palantir's team", []) == ["palantir"]
    assert jenga.contains("gaining 2K+ downloads", "2,000")
    assert jenga.contains("over 2,000 downloads", "2k+")
    assert not jenga.contains("2,500 downloads", "2")


def test_tower_and_restart_recovery():
    import app as webapp
    lines = [{"page": 1, "line": n, "role": r, "text": f"t{n}", "unique_terms": []}
             for n, r in [(9, "dead weight"), (10, "dead weight"), (11, "load-bearing"),
                          (12, "dead weight"), (14, "dead weight")]]
    assert [b["label"] for b in webapp.tower({"cited_lines": lines})] == ["L9–10", "L11", "L12", "L14"]
    with tempfile.TemporaryDirectory() as tmp:
        old = webapp.app.config["DATA_DIR"]
        webapp.app.config["DATA_DIR"] = Path(tmp)
        try:
            for rid, meta in [("aaaaaaaaaaaa", {"status": "running"}),
                              ("bbbbbbbbbbbb", {"status": "done", "judge_status": "running"})]:
                (Path(tmp) / "runs" / rid).mkdir(parents=True)
                (Path(tmp) / "runs" / rid / "meta.json").write_text(json.dumps({"id": rid, **meta}))
            webapp.recover_interrupted()
            assert webapp.load_meta("aaaaaaaaaaaa")["status"] == "error"
            assert webapp.load_meta("bbbbbbbbbbbb")["judge_status"] == "error"
        finally:
            webapp.app.config["DATA_DIR"] = old


class FakeClaude:
    """Mimics anthropic.Anthropic().messages.create: a scripted agent, and
    canned JSON for the judge's structured-output call."""

    def __init__(self, judge_json=None):
        self.messages = self
        self.requests = []
        self.judge_json = judge_json

    def create(self, **kw):
        self.requests.append(kw)
        usage = NS(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                   cache_creation_input_tokens=0)
        if "format" in kw.get("output_config", {}):   # the judge
            return NS(content=[NS(type="text", text=json.dumps(self.judge_json(kw)))],
                      stop_reason="end_turn", usage=usage, model="claude-haiku-5-5")
        if len(kw["messages"]) == 1:
            content = [NS(type="thinking", thinking="I should search for FastAPI."),
                       NS(type="tool_use", id="tu1", name="search_pdf",
                          input={"filename": "resume.pdf", "query": "FastAPI"})]
            return NS(content=content, stop_reason="tool_use", usage=usage, model=kw["model"])
        return NS(content=[NS(type="text", text="- You built a REST API with FastAPI and "
                                                "PostgreSQL [resume.pdf, P1-L11]")],
                  stop_reason="end_turn", usage=usage, model=kw["model"])


def test_claude_agent_loop_and_judge():
    import judge
    import llm

    def verdicts(kw):  # the judge supports the fact with the line numbered for P1-L11
        n = re.search(r"#(\d+) \[p1 L11\]", kw["messages"][0]["content"]).group(1)
        return {"claims": [{"id": "c1", "kind": "document fact", "note": "", "atoms": [
            {"fact": "Built a REST API with FastAPI", "status": "supported", "lines": [int(n)]},
            {"fact": "Used PostgreSQL", "status": "supported", "lines": [int(n)]}]}]}

    client = FakeClaude(verdicts)
    with tempfile.TemporaryDirectory() as tmp:
        out = agent4.run_agent(QUESTION, files_dir=HERE / "ai_files", client=client,
                               model="claude-opus-5-5", log_file=Path(tmp) / "a.log",
                               runs_dir=Path(tmp) / "runs")
    first, second = client.requests[:2]
    assert first["output_config"] == {"effort": llm.agent_effort()}
    assert first["thinking"]["type"] == "adaptive" and first["tools"][0]["strict"] is True
    # assistant content passed back unchanged, tool results in one user turn
    assert second["messages"][1]["role"] == "assistant"
    assert second["messages"][2]["content"][0]["type"] == "tool_result"
    assert out["usage"]["output_tokens"] == 100 and out["cost_usd"] > 0
    assert "MODEL REASONING:" in out["log_text"] and "USAGE:" in out["log_text"]

    run = jenga.parse_log(out["log_text"])[-1]
    rep = jenga.analyse_run(run)
    assert rep["claims"][0]["alibi"] == "supported"
    j = judge.judge_run(run, rep, client)
    c1 = j["claims"]["c1"]
    assert c1["verdict"] == "supported" and (c1["n_supported"], c1["n_atoms"]) == (2, 2)
    assert c1["atoms"][0]["refs"] == ["p1 L11"] and judge.is_current(j, rep)
    # verdicts are counted from the atoms
    s = judge.score_claim({"kind": "document fact", "atoms": [
        {"fact": "a", "status": "supported", "lines": [3]},
        {"fact": "b", "status": "supported", "lines": [9]}]}, [3])
    assert s["verdict"] == "miscited" and s["cited_used"] == [3]
    s = judge.score_claim({"kind": "judgement", "atoms": [
        {"fact": "a", "status": "supported", "lines": [3]},
        {"fact": "b", "status": "not found", "lines": []}]}, [])
    assert s["verdict"] == "not found" and s["kind"] == "judgement"   # worst fact decides
    s = judge.score_claim({"kind": "outside knowledge", "atoms": []}, [])
    assert s["verdict"] == "not found"                               # nothing checkable
    assert set(judge.STYLE) == set(judge.VERDICTS) == {"supported", "miscited",
                                                       "not found", "contradicted"}
    # judge.json written under the old names is recounted to the four
    old = {"claims": {"c1": {"kind": "judgement", "verdict": "partly grounded judgement",
                             "cited_lines": [3], "atoms": [
                                 {"fact": "a", "status": "supported", "lines": [3]},
                                 {"fact": "b", "status": "supported", "lines": [9]}]}},
           "totals": {"atoms": 2, "supported": 2}}
    judge.rescore(old)
    assert old["claims"]["c1"]["verdict"] == "miscited"
    assert old["totals"]["verdicts"] == {"miscited": 1} and old["totals"]["ci"]
    s = judge.score_claim({"kind": "document fact", "atoms": [
        {"fact": "a", "status": "contradicted", "lines": [3]}]}, [3])
    assert s["verdict"] == "contradicted"
    s = judge.score_claim({"kind": "document fact", "atoms": [   # "doesn't mention X"
        {"fact": "no mention of clearance", "status": "supported", "lines": []}]}, [])
    assert s["verdict"] == "supported"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
