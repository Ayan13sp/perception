"""Shared state object that flows through every node of the grading graph."""
import operator
from typing import Annotated, Optional, TypedDict


class GradingState(TypedDict, total=False):
    # ── Inputs ────────────────────────────────────────────────────
    question: str
    question_set_id: str
    student_answer: str
    model_answer: str
    has_references: bool
    teacher_note: Optional[str]  # trusted extra instructions from a regrade request

    # ── Gatekeeper ────────────────────────────────────────────────
    injection_suspected: bool  # regex pre-filter hit
    injection_flag: bool       # confirmed by the classifier -> short-circuit

    # ── Decomposer / fact-checker ─────────────────────────────────
    rubric_criteria: list[dict]  # [{id, description, points}], points sum to 10
    rubric_generated: bool       # True if created this run (caller should cache it)
    retrieved_context: list[dict]  # tool calls: [{tool, input, output}]
    fact_check_summary: str

    # ── Primary grader ────────────────────────────────────────────
    criterion_scores: list[dict]  # [{criterion_id, awarded, max_points, justification, confidence}]
    draft_score: float
    # Snapshot of the primary grader's output before any critic adjustment,
    # so Grader-vs-Critic disagreement can be measured later.
    grader_criterion_scores: list[dict]
    grader_draft_score: float
    confidence_score: float
    borderline: bool
    feedback: str

    # ── Critic / auditor ──────────────────────────────────────────
    critic_verdict: Optional[dict]
    needs_review: bool  # surface to the teacher for manual review

    # ── Output ────────────────────────────────────────────────────
    final_score: int
    error: Optional[str]

    # Append-only fields: each node returns only its own entries and
    # LangGraph merges them with operator.add.
    audit_notes: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]  # [{node, ms}]
    total_tokens: Annotated[int, operator.add]
