"""Thin async wrapper around Groq: validated JSON calls and raw tool-calling calls."""
import json
import os
from functools import lru_cache
from typing import Type, TypeVar

from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_client() -> AsyncGroq:
    api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set.")
    return AsyncGroq(api_key=api_key)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def chat_json(
    *,
    model: str,
    system: str,
    user: str,
    schema: Type[T],
    temperature: float = 0.1,
    retries: int = 1,
) -> tuple[T, int]:
    """
    Call the model in JSON mode and validate the reply against `schema`.
    On a parse/validation failure the error is fed back to the model once.
    Returns (parsed_object, total_tokens_used).
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    tokens = 0
    last_error: Exception | None = None

    for _ in range(retries + 1):
        completion = await get_client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        if completion.usage:
            tokens += completion.usage.total_tokens or 0
        content = _strip_fences(completion.choices[0].message.content or "")
        try:
            return schema.model_validate(json.loads(content)), tokens
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Your reply was invalid ({type(e).__name__}: {str(e)[:300]}). "
                    "Reply again with ONLY a JSON object matching the requested format.",
                },
            ]

    raise LLMError(f"Model output failed validation: {last_error}")


async def chat_with_tools(*, model: str, messages: list, tools: list, tool_choice: str = "auto"):
    """One tool-calling round. Returns (assistant_message, total_tokens_used)."""
    completion = await get_client().chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=0.1,
    )
    tokens = completion.usage.total_tokens if completion.usage else 0
    return completion.choices[0].message, tokens or 0
