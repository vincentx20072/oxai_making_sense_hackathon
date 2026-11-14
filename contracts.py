"""
Shared data shapes for every module (owned by Dev 1).

Change only via `CONTRACT contracts.py: <old> -> <new>` in the main room and a
reply from Vincent's agent. Shapes mirror the JSON written to disk
(report.json, replay/<claim_id>.json, eval/results.json).
"""

from typing import Literal, Optional, TypedDict

try:
    from typing import NotRequired
except ImportError:  # Python < 3.11
    from typing_extensions import NotRequired


# ---- agent trace -----------------------------------------------------------

class TraceStep(TypedDict, total=False):
    step: int
    kind: str              # "reasoning" | "call" | "result" | "select" | "answer"
    name: str              # tool name for calls
    args: dict
    text: str


class EvidenceSelection(TypedDict, total=False):
    filename: str
    page: int
    start_line: int
    end_line: int
    purpose: str


class AgentAnswer(TypedDict, total=False):
    run_id: str
    question: str
    model: str
    answer: str
    selected_evidence: list[EvidenceSelection]
    log_text: str


# ---- jenga report ----------------------------------------------------------

Verdict = Literal["grounded", "weakly grounded", "post-rationalisation risk",
                  "post-rationalised", "uncited", "unsupported"]
Alibi = Literal["supported", "unsupported", "phantom"]


class CitedLine(TypedDict):
    file: Optional[str]
    page: int
    line: int
    text: str
    status: str
    selected: bool
    terms: list[str]
    unique_terms: list[str]
    role: str


class Deduction(TypedDict):
    reason: str
    points: int


class TermInfo(TypedDict, total=False):
    term: str
    support: str           # "cited" | "elsewhere" | "none" ...
    in_question: bool
    first_own_step: Optional[int]
    first_own_source: Optional[str]
    first_evidence_step: Optional[int]
    asserted_before_seen: bool


class Claim(TypedDict):
    id: str
    text: str
    citations: list
    verdict: str           # a Verdict value
    score: int
    deductions: list[Deduction]
    E_lex: Optional[float]
    precision: Optional[float]
    terms: list[TermInfo]
    bridging_terms: list
    timing_terms: list
    cited_lines: list[CitedLine]
    causal: Optional[dict]  # replay_report block, or None
    alibi: NotRequired[Alibi]  # added in T10


class JengaReport(TypedDict, total=False):
    run_id: str
    question: str
    model: str
    label: Optional[str]
    answer: str
    question_terms: list[str]
    files: list
    steps: list
    term_marks: list
    selected_evidence: list
    claims: list[Claim]
    verdict_counts: dict[str, int]
    trust_score: Optional[int]
    trace_has_agent_text: bool
    notes: list[str]


# ---- replay ----------------------------------------------------------------

Magnitude = Literal["small", "medium", "large"]
ReplayMode = Literal["remove_all", "remove_each", "plant"]
CausalLabel = Literal["faithful", "post-rationalised", "unclear"]
RemovalOutcome = Literal["drops", "re-cited elsewhere", "survives uncited",
                         "cites removed lines"]
PlantOutcome = Literal["follows", "overrides and cites", "overrides without citing",
                       "abstains"]


class ReplayRequest(TypedDict, total=False):
    claim_id: str
    modes: list[ReplayMode]
    plant_override: Optional[str]
    magnitude: Magnitude
    samples: int


class RemovalResult(TypedDict):
    span: str              # e.g. "P1-L4-6" or "all"
    outcomes: list[str]    # RemovalOutcome values, one per sample
    survived: bool
    new_cites: list[str]


class PlantResult(TypedDict):
    original: str
    planted: str
    magnitude: Magnitude
    outcome: str           # PlantOutcome
    outcomes: list[str]


class CausalResult(TypedDict):
    label: CausalLabel
    E: Optional[float]
    F: Optional[float]
    removals: list[RemovalResult]
    remove_all_outcomes: list[str]
    plant: Optional[PlantResult]
    samples: int


class ReplayResult(TypedDict):
    run_id: str
    claim_id: str
    status: Literal["queued", "running", "done", "error"]
    cached: bool
    calls_used: int
    calls_estimated: int
    before_answer: str
    after_answers: dict[str, str]   # mode -> replayed answer text
    causal: CausalResult
    error: Optional[str]


# ---- eval ------------------------------------------------------------------

Family = Literal["known", "invented"]


class EvalQuestion(TypedDict):
    id: str
    family: Family
    question: str
    pdf: str               # file under eval/pdfs/
    text: str              # page text used to build the PDF
    value: str             # the fact the answer should contain
    plant: str             # the wrong value planted in the PDF


class EvalRow(TypedDict):
    id: str
    family: Family
    static_score: Optional[int]
    original_correct: bool
    plant_outcome: str
    remove_all_outcome: str
    evidence_insensitive: bool   # supported citation, but answer ignores evidence change
    label: CausalLabel


class RateCI(TypedDict):
    k: int
    n: int
    rate: Optional[float]
    lo: Optional[float]
    hi: Optional[float]


class EvalSummary(TypedDict):
    model: str
    dry_run: bool
    n: int
    overall: RateCI
    by_family: dict[str, RateCI]
    auroc: Optional[float]
    rows: list[EvalRow]
