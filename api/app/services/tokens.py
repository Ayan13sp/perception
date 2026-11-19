"""Personal access tokens (PATs) used by MCP clients such as Claude Code and Cursor."""
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from beanie import PydanticObjectId
from bson.errors import InvalidId

from app.db.database import ApiToken, User
from app.services import repo
from app.services.errors import ForbiddenError, InvalidRequestError, NotFoundError
from app.services.security import PAT_PREFIX, as_utc, hash_token, new_personal_access_token, utcnow

MAX_ACTIVE_TOKENS = 10
TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass
class VerifiedToken:
    token: ApiToken
    user: User


def is_active(token: ApiToken) -> bool:
    if token.revoked_at is not None:
        return False
    expires = as_utc(token.expires_at)
    return expires is None or expires > utcnow()


async def create_token(user: User, name: str, expires_in_days: int = 90) -> tuple[ApiToken, str]:
    active = [t for t in await repo.list_user_tokens(user.id) if is_active(t)]
    if len(active) >= MAX_ACTIVE_TOKENS:
        raise InvalidRequestError(f"You already have {MAX_ACTIVE_TOKENS} active tokens. Revoke one first.")

    raw, digest, prefix = new_personal_access_token()
    now = utcnow()
    token = await repo.create_token(
        user_id=user.id,
        name=name.strip()[:60] or "MCP client",
        token_hash=digest,
        prefix=prefix,
        role=user.role,
        created_at=now,
        expires_at=now + timedelta(days=expires_in_days),
    )
    return token, raw


async def list_tokens(user: User) -> list[ApiToken]:
    tokens = [t for t in await repo.list_user_tokens(user.id) if is_active(t)]
    return sorted(tokens, key=lambda t: as_utc(t.created_at), reverse=True)


async def revoke_token(user: User, token_id: str) -> None:
    try:
        token = await repo.get_token(PydanticObjectId(token_id))
    except (InvalidId, TypeError):
        raise NotFoundError("Token not found.")
    if token is None:
        raise NotFoundError("Token not found.")
    if token.user_id != user.id:
        raise ForbiddenError("You can only revoke your own tokens.")
    if token.revoked_at is None:
        await repo.update_token(token, {"revoked_at": utcnow()})


async def verify_token(raw: str) -> Optional[VerifiedToken]:
    """Resolve a raw PAT to its user. Returns None for unknown, revoked or expired tokens."""
    if not raw or not raw.startswith(PAT_PREFIX) or len(raw) > 200:
        return None
    token = await repo.find_token_by_hash(hash_token(raw))
    if token is None or not is_active(token):
        return None
    user = await repo.get_user(token.user_id)
    if user is None or user.role != token.role:
        return None  # user deleted, or role changed since the token was issued

    now = utcnow()
    last = as_utc(token.last_used_at)
    if last is None or now - last > TOUCH_INTERVAL:  # avoid a DB write on every call
        await repo.update_token(token, {"last_used_at": now})
    return VerifiedToken(token=token, user=user)
