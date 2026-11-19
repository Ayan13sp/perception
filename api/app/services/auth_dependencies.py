from typing import Optional

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db.database import User
from app.services import repo
from app.services.security import InvalidTokenError, decode_access_token

# auto_error=False so we return a consistent 401 (HTTPBearer's default is 403)
_bearer = HTTPBearer(auto_error=False, description="JWT access token from /auth/login")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> User:
    """Authenticate the request from a signed JWT in the Authorization header."""
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = PydanticObjectId(claims["sub"])
    except (InvalidTokenError, InvalidId, KeyError, TypeError):
        raise _unauthorized("Invalid or expired token")

    user = await repo.get_user(user_id)
    if user is None or user.role != claims.get("role"):
        raise _unauthorized("Invalid or expired token")
    return user
