"""Grading orchestration shared by new submissions (REST) and regrades (MCP)."""
from typing import Awaitable, Callable, Optional

from app.core.config import settings
from app.db.database import QuestionSet, User
from app.services import access, repo
from app.services.ai_service import get_ai_evaluation
from app.services.errors import InvalidRequestError, UpstreamError
from app.services.exams import model_answer_hash
from app.services.grading import run_grading_graph
from app.services.grading.nodes import MAX_TEACHER_NOTE
from app.services.grading.tools import has_reference_material
from app.services.security import utcnow

ProgressCallback = Callable[[str], Awaitable[None]]


async def grade_answer(
    exam: QuestionSet,
    student_answer: str,
    *,
    teacher_note: Optional[str] = None,
    on_node: Optional[ProgressCallback] = None,
) -> dict:
    """Grade one answer against an exam. Caches a newly generated rubric on the exam."""
    if settings.GRADING_PIPELINE == "legacy" and not teacher_note:
        evaluation = await get_ai_evaluation(exam.model_answer, student_answer)
        evaluation["trace"] = {"pipeline": "legacy"}
    else:
        model_hash = model_answer_hash(exam.model_answer)
        cached = exam.rubric_criteria if exam.rubric_hash == model_hash else None
        evaluation = await run_grading_graph(
            question=exam.question,
            model_answer=exam.model_answer,
            student_answer=student_answer,
            question_set_id=exam.id,
            rubric_criteria=cached,
            has_references=await has_reference_material(exam.id),
            teacher_note=teacher_note,
            on_node=on_node,
        )
        # Cache the decomposed rubric so later submissions skip that LLM call
        if evaluation.get("rubric_generated"):
            await repo.update_exam(exam, {"rubric_criteria": evaluation["rubric_criteria"], "rubric_hash": model_hash})

    if evaluation.get("score") == -1:
        raise UpstreamError(evaluation.get("feedback") or "AI evaluation failed")
    return evaluation


def _trace_summary(trace: Optional[dict]) -> Optional[dict]:
    """Compact copy of an old trace for the regrade history (full traces are large)."""
    if not trace:
        return None
    verdict = trace.get("critic_verdict") or {}
    return {
        "pipeline": trace.get("pipeline"),
        "path": trace.get("path"),
        "confidence_score": trace.get("confidence_score"),
        "criterion_scores": trace.get("criterion_scores"),
        "score_discrepancy": trace.get("score_discrepancy"),
        "critic_verdict": {"verdict": verdict.get("verdict"), "issues": verdict.get("issues", [])} if verdict else None,
        "teacher_note": trace.get("teacher_note"),
    }


async def regrade_submission(
    teacher: User,
    submission_id: str,
    note: Optional[str],
    *,
    on_node: Optional[ProgressCallback] = None,
) -> dict:
    """
    Rerun the grading graph on one submission with the teacher's extra instructions.
    The previous grade is kept in `regrade_history`; the teacher's `final_score` is never touched.
    """
    submission, exam = await access.get_teacher_submission(submission_id, teacher)
    note = (note or "").strip()
    if len(note) > MAX_TEACHER_NOTE:
        raise InvalidRequestError(f"updated_rubric_note must be at most {MAX_TEACHER_NOTE} characters.")

    result = await grade_answer(exam, submission.student_answer, teacher_note=note or None, on_node=on_node)

    history = list(submission.regrade_history or [])
    history.append({
        "ai_score": submission.ai_score,
        "ai_feedback": submission.ai_feedback,
        "grading_trace": _trace_summary(submission.grading_trace),
        "replaced_at": utcnow(),
        "regraded_by": str(teacher.id),
        "note": note,
    })
    previous = submission.ai_score
    await repo.update_submission(submission, {
        "ai_score": result["score"],
        "ai_feedback": result["feedback"],
        "grading_trace": result.get("trace"),
        "regrade_history": history,
    })

    trace = result.get("trace") or {}
    return {
        "submission_id": str(submission.id),
        "previous_ai_score": previous,
        "new_ai_score": result["score"],
        "final_score": submission.final_score,  # unchanged: only the teacher sets this
        "feedback": result["feedback"],
        "criterion_scores": trace.get("criterion_scores", []),
        "routed_to_critic": trace.get("routed_to_critic"),
        "needs_review": trace.get("needs_review"),
        "audit_notes": trace.get("audit_notes", []),
        "regrade_count": len(history),
    }
