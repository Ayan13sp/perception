import hashlib
import secrets
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.db.database import ApiToken, User
from app.models.user_models import UserOut
from app.services import tokens as token_service
from app.services.auth_dependencies import get_current_user
from app.services.security import create_access_token

router = APIRouter()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return f"{salt}${key.hex()}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        if '$' in hashed_password:
            salt, key_hex = hashed_password.split('$', 1)
            new_key = hashlib.pbkdf2_hmac(
                'sha256',
                plain_password.encode('utf-8'),
                salt.encode('utf-8'),
                100000
            )
            return secrets.compare_digest(new_key.hex(), key_hex)
        else:
            try:
                import bcrypt
                return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
            except Exception:
                return False
    except Exception:
        return False

Role = Literal["teacher", "student"]

class SignupRequest(BaseModel):
    username: str
    password: str
    role: Role

class LoginRequest(BaseModel):
    username: str
    password: str
    role: Role

class AuthResponse(UserOut):
    """The user plus a signed JWT to send as `Authorization: Bearer <access_token>`."""
    access_token: str
    token_type: str = "bearer"

def _auth_response(user: User) -> AuthResponse:
    return AuthResponse(
        id=str(user.id),
        username=user.username,
        email=user.email,
        role=user.role,
        access_token=create_access_token(str(user.id), user.role),
    )

@router.post("/signup", response_model=AuthResponse)
async def signup(req: SignupRequest):
    existing_user = await User.find_one(User.username == req.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )

    hashed_pwd = hash_password(req.password)
    user = User(
        username=req.username,
        role=req.role,
        hashed_password=hashed_pwd
    )
    await user.insert()
    return _auth_response(user)

@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    user = await User.find_one(User.username == req.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    if not user.hashed_password or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    if user.role != req.role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User exists but is a {user.role}, not a {req.role}"
        )

    return _auth_response(user)

# ----------------- Personal access tokens (for MCP clients) -----------------

class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="e.g. 'Claude Code on my laptop'")
    expires_in_days: int = Field(90, ge=1, le=365)

class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    role: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None

class TokenCreatedOut(TokenOut):
    token: str = Field(..., description="Shown only once. Store it in your MCP client config.")

def _token_out(token: ApiToken) -> TokenOut:
    return TokenOut(
        id=str(token.id),
        name=token.name,
        prefix=token.prefix,
        role=token.role,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
    )

@router.post("/tokens", response_model=TokenCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_api_token(body: TokenCreate, current_user: User = Depends(get_current_user)):
    token, raw = await token_service.create_token(current_user, body.name, body.expires_in_days)
    return TokenCreatedOut(**_token_out(token).model_dump(), token=raw)

@router.get("/tokens", response_model=List[TokenOut])
async def list_api_tokens(current_user: User = Depends(get_current_user)):
    return [_token_out(t) for t in await token_service.list_tokens(current_user)]

@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(token_id: str, current_user: User = Depends(get_current_user)):
    await token_service.revoke_token(current_user, token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
