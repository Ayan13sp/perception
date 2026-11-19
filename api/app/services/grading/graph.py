"""Builds and runs the stateful multi-agent grading graph (LangGraph)."""
import time
from typing import Awaitable, Callable, Optional

from langgraph.graph import END, START, StateGraph

from app.core.config import settings

from . import nodes
from .state import GradingState

NodeFn = Callable[[GradingState], Awaitable[dict]]


def _timed(name: str, fn: NodeFn) -> NodeFn:
    """Record per-node latency into the trace for observability."""
    async def wrapper(state: GradingState) -> dict:
        start = time.perf_counter()
        update = dict(await fn(state) or {})
        update["trace"] = [{"node": name, "ms": round((time.perf_counter() - start) * 1000)}]
        return update
    return wrapper


def build_graph():
    g = StateGraph(GradingState)
    for name, fn in [
        ("gatekeeper", nodes.gatekeeper),
        ("decompose_rubric", nodes.decompose_rubric),
        ("fact_check", nodes.fact_check),
        ("grade", nodes.grade),
        ("critic", nodes.critic),
        ("finalize", nodes.finalize),
    ]:
        g.add_node(name, _timed(name, fn))

    g.add_edge(START, "gatekeeper")
    g.add_conditional_edges(
        "gatekeeper",
        lambda s: "finalize" if s.get("injection_flag") else "decompose_rubric",
        {"finalize": "finalize", "decompose_rubric": "decompose_rubric"},
    )
    g.add_conditional_edges(
        "decompose_rubric",
        lambda s: "fact_check" if nodes.needs_fact_check(s) else "grade",
        {"fact_check": "fact_check", "grade": "grade"},
    )
    g.add_edge("fact_check", "grade")
    g.add_conditional_edges("grade", nodes.route_after_grade, {"critic": "critic", "finalize": "finalize"})
    g.add_edge("critic", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


GRADING_GRAPH = build_graph()


ProgressCallback = Callable[[str], Awaitable[None]]


async def run_grading_graph(
    *,
    question: str,
    model_answer: str,
    student_answer: str,
    question_set_id: Optional[object] = None,
    rubric_criteria: Optional[list[dict]] = None,
    has_references: bool = False,
    teacher_note: Optional[str] = None,
    on_node: Optional[ProgressCallback] = None,
) -> dict:
    """
    Run the full pipeline. Returns:
        {score, feedback, rubric_criteria, rubric_generated, trace}
    where score == -1 signals an unrecoverable grading error.

    `teacher_note` adds trusted instructions for the grader and critic (regrades).
    `on_node` is awaited with each node's name as it finishes (progress reporting).
    """
    started = time.perf_counter()
    initial: GradingState = {
        "question": question,
        "model_answer": model_answer,
        "student_answer": student_answer,
        "question_set_id": question_set_id,
        "has_references": has_references,
        "teacher_note": (teacher_note or "").strip()[: nodes.MAX_TEACHER_NOTE] or None,
        "rubric_criteria": rubric_criteria or [],
        "retrieved_context": [],
        "audit_notes": [],
        "trace": [],
        "total_tokens": 0,
        "needs_review": False,
    }
    final: GradingState = initial
    reported = 0
    try:
        # stream_mode="values" yields the full state after every step; the last one is final
        async for snapshot in GRADING_GRAPH.astream(initial, stream_mode="values"):
            final = snapshot
            steps = snapshot.get("trace", [])
            if on_node and len(steps) > reported:
                for step in steps[reported:]:
                    try:
                        await on_node(step["node"])
                    except Exception as e:  # progress is best-effort, never fails grading
                        print(f"Progress callback failed: {e}")
                reported = len(steps)
    except Exception as e:
        print(f"Grading graph crashed: {e}")
        return {"score": -1, "feedback": f"AI evaluation error ({type(e).__name__}): {e}"}

    path = [t["node"] for t in final.get("trace", [])]
    grader_draft = final.get("grader_draft_score")
    post_critic = final.get("draft_score")
    discrepancy = (
        round(abs(post_critic - grader_draft), 2)
        if "critic" in path and grader_draft is not None and post_critic is not None
        else (0.0 if grader_draft is not None else None)
    )
    trace = {
        "pipeline": "graph",
        "path": path,
        "node_latency_ms": {t["node"]: t["ms"] for t in final.get("trace", [])},
        "total_latency_ms": round((time.perf_counter() - started) * 1000),
        "total_tokens": final.get("total_tokens", 0),
        "models": {"grader": settings.GRADER_MODEL, "critic": settings.CRITIC_MODEL,
                   "gatekeeper": settings.GATEKEEPER_MODEL},
        "injection_suspected": final.get("injection_suspected", False),
        "injection_flag": final.get("injection_flag", False),
        "rubric_criteria": final.get("rubric_criteria", []),
        "criterion_scores": final.get("criterion_scores", []),
        "grader_criterion_scores": final.get("grader_criterion_scores", []),
        "grader_draft_score": grader_draft,
        "post_critic_score": post_critic,
        "score_discrepancy": discrepancy,
        "teacher_note": final.get("teacher_note"),
        "confidence_score": final.get("confidence_score"),
        "borderline": final.get("borderline", False),
        "routed_to_critic": "critic" in path,
        "critic_verdict": final.get("critic_verdict"),
        "retrieved_context": final.get("retrieved_context", []),
        "fact_check_summary": final.get("fact_check_summary", ""),
        "audit_notes": final.get("audit_notes", []),
        "needs_review": final.get("needs_review", False),
    }
    return {
        "score": final.get("final_score", -1),
        "feedback": final.get("feedback", ""),
        "rubric_criteria": final.get("rubric_criteria", []),
        "rubric_generated": final.get("rubric_generated", False),
        "trace": trace,
    }
