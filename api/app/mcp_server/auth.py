"""
Who is calling? Authentication for the MCP server.

- HTTP (remote clients): `Authorization: Bearer pcp_...` personal access tokens,
  checked by `PatTokenVerifier` in the SDK's bearer middleware on every request.
- stdio (local clients such as Claude Desktop): the token comes from the
  PERCEPTION_TOKEN environment variable and is re-verified on every call, so a
  revoked token stops working immediately.
"""
from typing import Awaitable, Callable, Optional

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from app.db.database import User
from app.services import repo, tokens
from app.services.access import parse_object_id
from app.services.errors import DomainError
from app.services.security import as_utc

UserResolver = Callable[[Optional[Context]], Awaitable[User]]


class NotAuthenticated(ToolError):
    pass


class PatTokenVerifier(TokenVerifier):
    """Validates Perception personal access tokens for the SDK's bearer-auth middleware."""

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        verified = await tokens.verify_token(token)
        if verified is None:
            return None
        expires = as_utc(verified.token.expires_at)
        return AccessToken(
            token=token,
            client_id=str(verified.token.id),
            scopes=[verified.user.role],
            subject=str(verified.user.id),
            expires_at=int(expires.timestamp()) if expires else None,
        )


def _token_from_request(ctx: Optional[Context]) -> Optional[AccessToken]:
    """Fallback: the authenticated user the bearer middleware put on the HTTP request."""
    try:
        request = ctx.request_context.request if ctx is not None else None
    except Exception:
        return None
    scope = getattr(request, "scope", None) or {}
    return getattr(scope.get("user"), "access_token", None)


async def resolve_http_user(ctx: Optional[Context] = None) -> User:
    token = get_access_token() or _token_from_request(ctx)
    if token is None or not token.subject:
        raise NotAuthenticated("Not authenticated. Send 'Authorization: Bearer <Perception access token>'.")
    try:
        user = await repo.get_user(parse_object_id(token.subject, "user"))
    except DomainError:
        user = None
    if user is None or user.role not in token.scopes:
        raise NotAuthenticated("Your access token is no longer valid. Create a new one in Perception.")
    return user


def stdio_token_resolver(raw_token: str) -> UserResolver:
    async def resolve(ctx: Optional[Context] = None) -> User:
        verified = await tokens.verify_token(raw_token)
        if verified is None:
            raise NotAuthenticated("PERCEPTION_TOKEN is invalid, expired or revoked.")
        return verified.user
    return resolve
