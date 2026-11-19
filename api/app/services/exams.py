"""Exam (question set) authoring and lookup, shared by REST and MCP."""
import hashlib
from collections import Counter
from typing import Optional

from pydantic import ValidationError

from app.db.database import QuestionSet, User
from app.models.teacher_models import QuestionSetCreate
from app.services import access, repo
from app.services.errors import InvalidRequestError, UpstreamError
from app.services.grading.nodes import _normalise_rubric, generate_rubric
from app.services.grading.schemas import Rubric


def model_answer_hash(text: str) -> str:
    """Cache key tying a stored rubric to the exact model answer it was generated from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_rubric(exam: QuestionSet) -> list[dict]:
    """The cached rubric, or [] if none exists yet or the model answer changed since."""
    if exam.rubric_criteria and exam.rubric_hash == model_answer_hash(exam.model_answer):
        return exam.rubric_criteria
    return []


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    where = ".".join(str(p) for p in err.get("loc", ()))
    return f"{where}: {err.get('msg')}" if where else err.get("msg", str(e))


def validate_rubric(criteria: list[dict]) -> list[dict]:
    """Validate teacher-supplied criteria and normalise their points to total 10."""
    try:
        rubric = Rubric.model_validate({"criteria": criteria})
    except ValidationError as e:
        raise InvalidRequestError(f"Invalid rubric ({_first_error(e)}).")
    return _normalise_rubric([c.model_dump() for c in rubric.criteria])


async def draft_rubric(question: str, model_answer: str) -> list[dict]:
    """Run the Rubric Decomposer agent without saving anything."""
    if len((model_answer or "").strip()) < 10:
        raise InvalidRequestError("model_answer must be at least 10 characters.")
    try:
        criteria, _ = await generate_rubric(question or "", model_answer)
    except Exception as e:
        raise UpstreamError(f"Rubric generation failed ({type(e).__name__}). Try again.")
    return criteria


def _clean_usernames(usernames: Optional[list[str]]) -> list[str]:
    seen, out = set(), []
    for name in usernames or []:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


async def create_exam(
    teacher: User,
    *,
    title: str,
    question: str,
    model_answer: str,
    assigned_usernames: Optional[list[str]] = None,
    rubric_criteria: Optional[list[dict]] = None,
    generate_rubric_now: bool = False,
) -> tuple[QuestionSet, list[str]]:
    """Create an exam. Returns (exam, unknown_usernames)."""
    access.require_role(teacher, "teacher", "create question sets")
    try:
        data = QuestionSetCreate(title=title, question=question, model_answer=model_answer,
                                 assigned_usernames=assigned_usernames)
    except ValidationError as e:
        raise InvalidRequestError(f"Invalid question set ({_first_error(e)}).")

    usernames = _clean_usernames(data.assigned_usernames)
    students = await repo.find_students_by_usernames(usernames)
    found = {s.username for s in students}
    unknown = [u for u in usernames if u not in found]
    if usernames and not students:
        # An empty assignment list means "open to every student": never let typos do that
        raise InvalidRequestError(
            f"None of these students exist: {', '.join(unknown)}. Not creating an exam open to everyone."
        )

    if rubric_criteria:
        criteria = validate_rubric(rubric_criteria)
    elif generate_rubric_now:
        criteria = await draft_rubric(data.question, data.model_answer)
    else:
        criteria = None  # generated lazily on the first submission

    exam = await repo.create_exam(
        title=data.title,
        question=data.question,
        model_answer=data.model_answer,
        creator=teacher,
        assigned_students=students,
        rubric_criteria=criteria,
        rubric_hash=model_answer_hash(data.model_answer) if criteria else None,
    )
    return exam, unknown


async def list_exams_for(user: User) -> list[dict]:
    """Exams visible to this user: a teacher's own, or a student's assigned/open ones."""
    if user.role == "teacher":
        exams = await repo.list_teacher_exams(user.id)
        subs = await repo.list_exams_submissions([e.id for e in exams])
        counts = Counter(repo.link_id(s.question_set) for s in subs)
        return [
            {
                "exam_id": str(e.id),
                "title": e.title,
                "question": e.question,
                "assigned_students": len(e.assigned_students or []) or "all students",
                "submissions": counts.get(e.id, 0),
                "rubric_ready": bool(current_rubric(e)),
            }
            for e in exams
        ]

    access.require_role(user, "student", "list exams")
    exams = await repo.list_open_or_assigned_exams(user.id)
    submitted = {repo.link_id(s.question_set) for s in await repo.list_student_submissions(user.id)}
    teachers = await repo.get_users(repo.link_id(e.creator) for e in exams)
    return [
        {
            "exam_id": str(e.id),
            "title": e.title,
            "question": e.question,  # never the model answer
            "teacher": getattr(teachers.get(repo.link_id(e.creator)), "username", "unknown"),
            "status": "submitted" if e.id in submitted else "not_submitted",
        }
        for e in exams
    ]


async def get_exam_detail(teacher: User, exam_id: str) -> dict:
    """Teacher-only view: model answer, rubric and reference material."""
    exam = await access.get_teacher_exam(exam_id, teacher)
    rubric = current_rubric(exam)
    students = await repo.get_users(repo.link_id(s) for s in exam.assigned_students or [])
    return {
        "exam_id": str(exam.id),
        "title": exam.title,
        "question": exam.question,
        "model_answer": exam.model_answer,
        "rubric_criteria": rubric,
        "rubric_status": "ready" if rubric else "not generated yet (created on the first graded submission)",
        "assigned_students": sorted(u.username for u in students.values()) or "all students",
        "reference_sources": await repo.list_reference_sources(exam.id),
    }
