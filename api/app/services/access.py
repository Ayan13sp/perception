"""
Authorization checks shared by the REST routes and the MCP server.

Every check fails closed: if ownership or assignment can't be positively
confirmed, access is denied.
"""
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId

from app.db.database import QuestionSet, Submission, User
from app.services import repo
from app.services.errors import ForbiddenError, InvalidRequestError, NotFoundError


def parse_object_id(value: Any, what: str) -> PydanticObjectId:
    try:
        return PydanticObjectId(str(value).strip())
    except (InvalidId, TypeError, ValueError):
        raise InvalidRequestError(f"Invalid {what} ID format.")


def require_role(user: User, role: str, action: str = "do this") -> None:
    if user.role != role:
        raise ForbiddenError(f"Only {role}s can {action}.")


def is_exam_owner(exam: QuestionSet, user: User) -> bool:
    owner = repo.link_id(getattr(exam, "creator", None))
    return owner is not None and owner == user.id


def student_can_access(exam: QuestionSet, user: User) -> bool:
    """An exam with no assignment list is open to every student."""
    assigned = [repo.link_id(s) for s in (getattr(exam, "assigned_students", None) or [])]
    return not assigned or user.id in assigned


async def get_teacher_exam(exam_id: Any, user: User) -> QuestionSet:
    require_role(user, "teacher", "manage question sets")
    exam = await repo.get_exam(parse_object_id(exam_id, "question set"))
    if exam is None:
        raise NotFoundError("Question set not found.")
    if not is_exam_owner(exam, user):
        raise ForbiddenError("Access denied to question sets from other teachers.")
    return exam


async def get_teacher_submission(submission_id: Any, user: User) -> tuple[Submission, QuestionSet]:
    """A submission plus its exam, only if the exam belongs to this teacher."""
    require_role(user, "teacher", "review submissions")
    submission = await repo.get_submission(parse_object_id(submission_id, "submission"))
    if submission is None:
        raise NotFoundError("Submission not found.")
    exam_id = repo.link_id(submission.question_set)
    exam = await repo.get_exam(exam_id) if exam_id else None
    if exam is None or not is_exam_owner(exam, user):
        raise ForbiddenError("Access denied to submissions for other teachers' question sets.")
    return submission, exam


async def get_student_exam(exam_id: Any, user: User) -> QuestionSet:
    require_role(user, "student", "answer question sets")
    exam = await repo.get_exam(parse_object_id(exam_id, "question set"))
    # Same error whether it doesn't exist or isn't assigned: don't reveal other classes' exams
    if exam is None or not student_can_access(exam, user):
        raise NotFoundError("Question set not found.")
    return exam


async def get_student_submission(submission_id: Any, user: User) -> Submission:
    require_role(user, "student", "view their own results")
    submission = await repo.get_submission(parse_object_id(submission_id, "submission"))
    if submission is None or repo.link_id(submission.student) != user.id:
        raise NotFoundError("Result not found.")
    return submission
