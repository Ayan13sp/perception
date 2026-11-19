"""
Data-access layer: every MongoDB query used by the services lives here.

Keeping queries in one module gives the services (and the MCP server) a single
seam that tests replace with an in-memory fake. Real queries, including the
DBRef "$id" lookups used throughout this codebase, are exercised end-to-end
against a real MongoDB (see README / scripts/seed_demo.py).
"""
from typing import Any, Iterable, Optional

from beanie import PydanticObjectId
from bson import ObjectId

from app.db.database import ApiToken, QuestionSet, ReferenceChunk, Submission, User


def link_id(value: Any) -> Optional[PydanticObjectId]:
    """ObjectId of a Document, Link, DBRef, ObjectId or id string; None if unknown."""
    if value is None:
        return None
    ref = getattr(value, "ref", None)  # beanie Link -> DBRef
    if ref is not None and getattr(ref, "id", None) is not None:
        return PydanticObjectId(ref.id)
    if isinstance(value, ObjectId):
        return PydanticObjectId(value)
    inner = getattr(value, "id", None)  # Document or DBRef
    if inner is not None:
        return PydanticObjectId(inner)
    if isinstance(value, str):
        try:
            return PydanticObjectId(value)
        except Exception:
            return None
    return None


# ───────────────────────────── users ─────────────────────────────

async def get_user(user_id: PydanticObjectId) -> Optional[User]:
    return await User.get(user_id)


async def get_users(user_ids: Iterable[PydanticObjectId]) -> dict[PydanticObjectId, User]:
    ids = list({i for i in user_ids if i is not None})
    if not ids:
        return {}
    return {u.id: u for u in await User.find({"_id": {"$in": ids}}).to_list()}


async def find_students_by_usernames(usernames: list[str]) -> list[User]:
    if not usernames:
        return []
    return await User.find({"username": {"$in": usernames}, "role": "student"}).to_list()


# ───────────────────────────── tokens ─────────────────────────────

async def create_token(**fields) -> ApiToken:
    token = ApiToken(**fields)
    await token.insert()
    return token


async def get_token(token_id: PydanticObjectId) -> Optional[ApiToken]:
    return await ApiToken.get(token_id)


async def find_token_by_hash(token_hash: str) -> Optional[ApiToken]:
    return await ApiToken.find_one({"token_hash": token_hash})


async def list_user_tokens(user_id: PydanticObjectId) -> list[ApiToken]:
    return await ApiToken.find({"user_id": user_id, "revoked_at": None}).to_list()


async def update_token(token: ApiToken, fields: dict) -> None:
    await token.set(fields)


# ───────────────────────────── exams ─────────────────────────────

async def get_exam(exam_id: PydanticObjectId) -> Optional[QuestionSet]:
    return await QuestionSet.get(exam_id)


async def get_exams(exam_ids: Iterable[PydanticObjectId]) -> dict[PydanticObjectId, QuestionSet]:
    ids = list({i for i in exam_ids if i is not None})
    if not ids:
        return {}
    return {e.id: e for e in await QuestionSet.find({"_id": {"$in": ids}}).to_list()}


async def list_teacher_exams(teacher_id: PydanticObjectId) -> list[QuestionSet]:
    # Links are stored as DBRefs; older documents may hold a bare ObjectId
    return await QuestionSet.find({"$or": [{"creator.$id": teacher_id}, {"creator": teacher_id}]}).to_list()


async def list_open_or_assigned_exams(student_id: PydanticObjectId) -> list[QuestionSet]:
    """Exams assigned to this student, plus exams with no assignment list (open to all)."""
    return await QuestionSet.find({
        "$or": [
            {"assigned_students": {"$size": 0}},
            {"assigned_students.$id": student_id},
            {"assigned_students": student_id},
        ]
    }).to_list()


async def create_exam(**fields) -> QuestionSet:
    exam = QuestionSet(**fields)
    await exam.insert()
    return exam


async def update_exam(exam: QuestionSet, fields: dict) -> None:
    await exam.set(fields)


# ─────────────────────────── submissions ───────────────────────────

async def get_submission(submission_id: PydanticObjectId) -> Optional[Submission]:
    return await Submission.get(submission_id)


async def list_exam_submissions(exam_id: PydanticObjectId) -> list[Submission]:
    return await Submission.find({"$or": [{"question_set.$id": exam_id}, {"question_set": exam_id}]}).to_list()


async def list_exams_submissions(exam_ids: list[PydanticObjectId]) -> list[Submission]:
    if not exam_ids:
        return []
    return await Submission.find({
        "$or": [{"question_set.$id": {"$in": exam_ids}}, {"question_set": {"$in": exam_ids}}]
    }).to_list()


async def list_student_submissions(student_id: PydanticObjectId) -> list[Submission]:
    return await Submission.find({"$or": [{"student.$id": student_id}, {"student": student_id}]}).to_list()


async def create_submission(**fields) -> Submission:
    submission = Submission(**fields)
    await submission.insert()
    return submission


async def update_submission(submission: Submission, fields: dict) -> None:
    await submission.set(fields)


# ─────────────────────────── reference material ───────────────────────────

async def list_reference_sources(exam_id: PydanticObjectId) -> list[dict]:
    summary: dict[str, dict] = {}
    for chunk in await ReferenceChunk.find({"question_set_id": exam_id}).to_list():
        entry = summary.setdefault(chunk.source_name, {
            "source_name": chunk.source_name, "chunks": 0, "characters": 0,
            "student_visible": chunk.student_visible,
        })
        entry["chunks"] += 1
        entry["characters"] += len(chunk.content)
    return list(summary.values())


async def replace_reference_source(exam_id: PydanticObjectId, source_name: str, chunks: list[str],
                                   student_visible: bool) -> None:
    await ReferenceChunk.find({"question_set_id": exam_id, "source_name": source_name}).delete()
    await ReferenceChunk.insert_many([
        ReferenceChunk(question_set_id=exam_id, source_name=source_name, chunk_index=i, content=c,
                       student_visible=student_visible)
        for i, c in enumerate(chunks)
    ])


async def delete_reference_source(exam_id: PydanticObjectId, source_name: str) -> None:
    await ReferenceChunk.find({"question_set_id": exam_id, "source_name": source_name}).delete()
