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
