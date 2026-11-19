"""
Run the Perception MCP server over stdio, for local clients such as Claude Desktop.

    cd api
    PERCEPTION_TOKEN=pcp_... DATABASE_URL=mongodb://... GROQ_API_KEY=... python -m app.mcp_server

Create the token in Perception (profile menu -> Connect AI assistant).
"""
import asyncio
import contextlib
import os
import sys

from app.db.database import init_db
from app.mcp_server.auth import stdio_token_resolver
from app.mcp_server.server import build_server
from app.services import tokens


async def main() -> None:
    raw_token = os.getenv("PERCEPTION_TOKEN", "").strip()
    if not raw_token:
        sys.exit("PERCEPTION_TOKEN is required. Create one in Perception: profile menu -> Connect AI assistant.")

    # stdout is the protocol channel: keep startup output off it
    with contextlib.redirect_stdout(sys.stderr):
        await init_db()
        verified = await tokens.verify_token(raw_token)
    if verified is None:
        sys.exit("PERCEPTION_TOKEN is invalid, expired or revoked.")
    print(f"Perception MCP server (stdio) running as {verified.user.username} ({verified.user.role})", file=sys.stderr)

    server = build_server(user_resolver=stdio_token_resolver(raw_token))
    await server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
