"""
Token primitives.

- Web app: short-lived, signed JWT access tokens (HS256).
- MCP clients: long-lived personal access tokens (PATs). Only a SHA-256 hash is
  stored; PATs are 256-bit random values, so a fast hash is appropriate
  (no dictionary to brute-force, unlike passwords).
"""
import hashlib
import secrets
import warnings
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT

from app.core.config import settings

PAT_PREFIX = "pcp_"
_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}
_INSECURE_SECRETS = {"", "default_secret", "your-super-secret-jwt-key-replace-in-production"}


class InvalidTokenError(Exception):
    pass


def _signing_key() -> str:
    if settings.SECRET_KEY in _INSECURE_SECRETS:
        # Never sign with a publicly known key: fall back to a per-process random
        # key (sessions reset on restart) and warn loudly.
        global _EPHEMERAL_KEY
        if _EPHEMERAL_KEY is None:
            warnings.warn("SECRET_KEY is unset or a placeholder; using an ephemeral key. Set SECRET_KEY in production.")
            _EPHEMERAL_KEY = secrets.token_urlsafe(48)
        return _EPHEMERAL_KEY
    return settings.SECRET_KEY


_EPHEMERAL_KEY: str | None = None
_ALGORITHM = settings.ALGORITHM if settings.ALGORITHM in _ALLOWED_ALGORITHMS else "HS256"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """MongoDB returns naive datetimes; treat them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_access_token(user_id: str, role: str, expires_minutes: int | None = None) -> str:
    now = utcnow()
    minutes = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, _signing_key(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            _signing_key(),
            algorithms=[_ALGORITHM],  # pinned: never trust the token's own "alg" header
            options={"require": ["sub", "exp", "iat"]},
        )
    except jwt.PyJWTError as e:
        raise InvalidTokenError(str(e)) from e
    if claims.get("typ") != "access":
        raise InvalidTokenError("wrong token type")
    return claims


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_personal_access_token() -> tuple[str, str, str]:
    """Returns (raw_token, sha256_hash, display_prefix). The raw token is shown once."""
    raw = PAT_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:12]
