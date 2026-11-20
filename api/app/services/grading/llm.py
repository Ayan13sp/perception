"""
Thin async wrapper around Groq: validated JSON calls and tool-calling calls, with
explicit retry/backoff and per-call metrics.

Every completion is recorded as an `LLMCall` (latency, backoff waits, tokens,
JSON validity) into the active `record_calls()` collector, if any. The eval
harness uses this to measure JSON validity rates and latency without patching
the pipeline.
"""
import asyncio
import json
import os
import random
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Iterator, Optional, Type, TypeVar

import groq
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class BudgetExhausted(LLMError):
    """A daily request/token quota was hit: retrying won't help until it resets."""


@dataclass
class LLMCall:
    purpose: str
    model: str
    latency_ms: float = 0.0        # time spent inside API attempts (excludes backoff sleeps)
    wait_ms: float = 0.0           # backoff sleeps after rate limits / transient errors
    attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    server_queue_ms: float = 0.0   # provider-side queueing (Groq usage.queue_time)
    server_total_ms: float = 0.0   # provider-side processing (Groq usage.total_time)
    json_valid: Optional[bool] = None  # JSON-mode calls: did this reply parse and validate?
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


_calls: ContextVar[Optional[list]] = ContextVar("llm_calls", default=None)


@contextmanager
def record_calls() -> Iterator[list[LLMCall]]:
    """Collect every LLM call made inside this block (including in child tasks)."""
    calls: list[LLMCall] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def _record(call: LLMCall) -> None:
    sink = _calls.get()
    if sink is not None:
        sink.append(call)


@lru_cache(maxsize=1)
def get_client() -> AsyncGroq:
    api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set.")
    # Retries are handled below so that backoff time can be measured and daily
    # quota errors can stop immediately instead of being retried.
    return AsyncGroq(api_key=api_key, max_retries=0)


_RETRYABLE = (groq.RateLimitError, groq.APIConnectionError, groq.APITimeoutError, groq.InternalServerError)
_DAILY_QUOTA = re.compile(r"per day|\(RPD\)|\(TPD\)", re.IGNORECASE)
_TRY_AGAIN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)
MAX_BACKOFF_S = 90.0


def _retry_delay(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return min(float(header), MAX_BACKOFF_S)
        except ValueError:
            pass
    match = _TRY_AGAIN.search(str(error))
    if match:
        return min(int(match.group(1) or 0) * 60 + float(match.group(2)), MAX_BACKOFF_S)
    return min(2 ** attempt + random.random(), MAX_BACKOFF_S)


def is_json_generation_error(error: Exception) -> bool:
    """Groq rejects JSON-mode output that isn't valid JSON with a 400 json_validate_failed."""
    return isinstance(error, groq.BadRequestError) and "json_validate_failed" in str(error)


async def _create(purpose: str, **kwargs):
    """One logical completion with retries. Returns (completion, LLMCall)."""
    call = LLMCall(purpose=purpose, model=kwargs.get("model", "?"))
    try:
        for attempt in range(1, settings.LLM_MAX_ATTEMPTS + 1):
            call.attempts = attempt
            start = time.perf_counter()
            try:
                completion = await get_client().chat.completions.create(**kwargs)
            except _RETRYABLE as e:
                call.latency_ms += (time.perf_counter() - start) * 1000
                if isinstance(e, groq.RateLimitError) and _DAILY_QUOTA.search(str(e)):
                    raise BudgetExhausted(str(e)[:300]) from e
                if attempt == settings.LLM_MAX_ATTEMPTS:
                    raise
                delay = _retry_delay(e, attempt)
                call.wait_ms += delay * 1000
                await asyncio.sleep(delay)
                continue
            call.latency_ms += (time.perf_counter() - start) * 1000
            usage = completion.usage
            if usage is not None:
                call.prompt_tokens = usage.prompt_tokens or 0
                call.completion_tokens = usage.completion_tokens or 0
                details = getattr(usage, "prompt_tokens_details", None)
                call.cached_tokens = getattr(details, "cached_tokens", 0) or 0
                completion_details = getattr(usage, "completion_tokens_details", None)
                call.reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0
                call.server_queue_ms = (getattr(usage, "queue_time", 0) or 0) * 1000
                call.server_total_ms = (getattr(usage, "total_time", 0) or 0) * 1000
            return completion, call
    except Exception as e:
        call.error = f"{type(e).__name__}: {e}"[:300]
        e.llm_call = call  # lets callers mark JSON failures on the recorded call
        raise
    finally:
        _record(call)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _total(call: LLMCall) -> int:
    return call.prompt_tokens + call.completion_tokens


async def chat_json(
    *,
    model: str,
    system: str,
    user: str,
    schema: Type[T],
    temperature: float = 0.1,
    retries: int = 1,
    purpose: str = "json",
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
        try:
            completion, call = await _create(purpose, model=model, messages=messages, temperature=temperature,
                                             response_format={"type": "json_object"})
        except groq.BadRequestError as e:
            if not is_json_generation_error(e):
                raise
            e.llm_call.json_valid = False
            last_error = e
            messages = messages[:2] + [{"role": "user", "content": user + "\n\nReply with ONLY a valid JSON object."}]
            continue

        tokens += _total(call)
        content = _strip_fences(completion.choices[0].message.content or "")
        try:
            parsed = schema.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as e:
            call.json_valid = False
            last_error = e
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Your reply was invalid ({type(e).__name__}: {str(e)[:300]}). "
                    "Reply again with ONLY a JSON object matching the requested format.",
                },
            ]
            continue
        call.json_valid = True
        return parsed, tokens

    raise LLMError(f"Model output failed validation: {last_error}")


async def chat_with_tools(*, model: str, messages: list, tools: list, tool_choice: str = "auto",
                          purpose: str = "tools", **extra):
    """One tool-calling round. Returns (assistant_message, total_tokens_used)."""
    completion, call = await _create(purpose, model=model, messages=messages, tools=tools,
                                     tool_choice=tool_choice, temperature=0.1, **extra)
    return completion.choices[0].message, _total(call)


async def chat_text(*, model: str, messages: list, purpose: str = "text", **kwargs) -> tuple[str, int]:
    """Plain completion (e.g. a classifier that returns a score as text)."""
    completion, call = await _create(purpose, model=model, messages=messages, **kwargs)
    return completion.choices[0].message.content or "", _total(call)
