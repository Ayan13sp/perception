"""JWT web auth, PAT lifecycle and the get_current_user dependency (no database needed)."""
from datetime import timedelta

import jwt
import pytest
from beanie import PydanticObjectId
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import ApiToken, User
from app.services import repo, tokens
from app.services.auth_dependencies import get_current_user
from app.services.errors import ForbiddenError
from app.services.security import (
    InvalidTokenError,
    PAT_PREFIX,
    create_access_token,
    decode_access_token,
    hash_token,
    new_personal_access_token,
    utcnow,
)


def make_user(role="teacher", username="t1"):
    return User.model_construct(id=PydanticObjectId(), username=username, role=role, email=None, hashed_password=None)


def make_token(user, raw, **overrides):
    fields = dict(
        id=PydanticObjectId(), user_id=user.id, name="test", token_hash=hash_token(raw), prefix=raw[:12],
        role=user.role, created_at=utcnow(), expires_at=utcnow() + timedelta(days=30),
        last_used_at=None, revoked_at=None,
    )
    fields.update(overrides)
    return ApiToken.model_construct(**fields)


# ───────────────────────────── JWT ─────────────────────────────

def test_jwt_round_trip():
    uid = str(PydanticObjectId())
    claims = decode_access_token(create_access_token(uid, "student"))
    assert claims["sub"] == uid and claims["role"] == "student"


def test_expired_jwt_rejected():
    with pytest.raises(InvalidTokenError):
        decode_access_token(create_access_token("abc", "teacher", expires_minutes=-1))


def test_tampered_jwt_rejected():
    token = create_access_token("abc", "student")
    header, payload, sig = token.split(".")
    forged = jwt.encode({"sub": "abc", "role": "teacher", "typ": "access", "iat": 0, "exp": 9999999999}, "attacker-chosen-secret-key-0123456789")
    with pytest.raises(InvalidTokenError):
        decode_access_token(".".join([header, forged.split(".")[1], sig]))


def test_wrong_secret_rejected(monkeypatch):
    token = create_access_token("abc", "teacher")
    monkeypatch.setattr(settings, "SECRET_KEY", "a-completely-different-secret-key-value")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_unsigned_alg_none_token_rejected():
    unsigned = jwt.encode({"sub": "abc", "role": "teacher", "typ": "access", "iat": 0, "exp": 9999999999},
                          key=None, algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_access_token(unsigned)


def test_non_access_token_type_rejected():
    now = utcnow()
    other = jwt.encode({"sub": "abc", "typ": "refresh", "iat": now, "exp": now + timedelta(hours=1)},
                       settings.SECRET_KEY, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(other)


# ───────────────────────── get_current_user ─────────────────────────

@pytest.fixture
def client(monkeypatch):
    users = {}

    async def fake_get_user(user_id):
        return users.get(user_id)

    monkeypatch.setattr(repo, "get_user", fake_get_user)
    app = FastAPI()

    @app.get("/me")
    async def me(user: User = Depends(get_current_user)):
        return {"id": str(user.id), "role": user.role}

    return TestClient(app), users


def test_bearer_jwt_authenticates(client):
    c, users = client
    user = make_user()
    users[user.id] = user
    r = c.get("/me", headers={"Authorization": f"Bearer {create_access_token(str(user.id), 'teacher')}"})
    assert r.status_code == 200 and r.json()["id"] == str(user.id)


def test_legacy_x_user_id_header_is_rejected(client):
    c, users = client
    user = make_user()
    users[user.id] = user
    r = c.get("/me", headers={"x-user-id": str(user.id)})
    assert r.status_code == 401


@pytest.mark.parametrize("header", ["Bearer not-a-jwt", "Basic dXNlcjpwYXNz", ""])
def test_bad_credentials_are_401(client, header):
    c, _ = client
    assert c.get("/me", headers={"Authorization": header} if header else {}).status_code == 401


def test_role_change_invalidates_jwt(client):
    c, users = client
    user = make_user(role="student")
    users[user.id] = user
    token = create_access_token(str(user.id), "teacher")  # claims a role the user doesn't have
    assert c.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ───────────────────────── personal access tokens ─────────────────────────

def test_pat_format_and_hash():
    raw, digest, prefix = new_personal_access_token()
    assert raw.startswith(PAT_PREFIX) and len(raw) > 40
    assert digest == hash_token(raw) and raw not in digest
    assert prefix == raw[:12]


@pytest.fixture
def token_store(monkeypatch):
    store = {"tokens": [], "users": {}, "updates": []}

    async def find_token_by_hash(h):
        return next((t for t in store["tokens"] if t.token_hash == h), None)

    async def get_user(uid):
        return store["users"].get(uid)

    async def get_token(tid):
        return next((t for t in store["tokens"] if t.id == tid), None)

    async def update_token(token, fields):
        store["updates"].append(fields)
        for k, v in fields.items():
            setattr(token, k, v)

    for name, fn in [("find_token_by_hash", find_token_by_hash), ("get_user", get_user),
                     ("get_token", get_token), ("update_token", update_token)]:
        monkeypatch.setattr(repo, name, fn)
    return store


async def test_valid_pat_resolves_user_and_records_use(token_store):
    user = make_user()
    raw = new_personal_access_token()[0]
    token_store["users"][user.id] = user
    token_store["tokens"].append(make_token(user, raw))
    verified = await tokens.verify_token(raw)
    assert verified and verified.user.id == user.id
    assert "last_used_at" in token_store["updates"][0]
    # A second call inside the throttle window doesn't write again
    await tokens.verify_token(raw)
    assert len(token_store["updates"]) == 1


@pytest.mark.parametrize("overrides", [
    {"revoked_at": utcnow()},
    {"expires_at": utcnow() - timedelta(minutes=1)},
    {"role": "student"},  # user's role changed after the token was issued
])
async def test_inactive_pats_rejected(token_store, overrides):
    user = make_user()
    raw = new_personal_access_token()[0]
    token_store["users"][user.id] = user
    token_store["tokens"].append(make_token(user, raw, **overrides))
    assert await tokens.verify_token(raw) is None


async def test_unknown_or_malformed_pat_rejected(token_store):
    assert await tokens.verify_token("pcp_doesnotexist") is None
    assert await tokens.verify_token("ghp_wrongprefix") is None
    assert await tokens.verify_token("") is None


async def test_cannot_revoke_another_users_token(token_store):
    owner, attacker = make_user(username="a"), make_user(username="b")
    raw = new_personal_access_token()[0]
    token = make_token(owner, raw)
    token_store["tokens"].append(token)
    with pytest.raises(ForbiddenError):
        await tokens.revoke_token(attacker, str(token.id))
    assert token.revoked_at is None
