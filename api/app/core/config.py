import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "default_secret")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 720))
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")

    # ── Agentic grading pipeline ──────────────────────────────────
    # "graph" = multi-agent LangGraph pipeline, "legacy" = single LLM call
    GRADING_PIPELINE: str = os.getenv("GRADING_PIPELINE", "graph")
    GRADER_MODEL: str = os.getenv("GRADER_MODEL", "openai/gpt-oss-120b")
    CRITIC_MODEL: str = os.getenv("CRITIC_MODEL", "openai/gpt-oss-120b")
    GATEKEEPER_MODEL: str = os.getenv("GATEKEEPER_MODEL", "meta-llama/llama-prompt-guard-2-86m")
    # Below this grader confidence, the Critic/Auditor agent reviews the draft
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", 0.85))
    # Scores within ±0.5 of this mark are "borderline" and always audited
    PASS_MARK: float = float(os.getenv("PASS_MARK", 5.0))
    MAX_TOOL_ROUNDS: int = int(os.getenv("MAX_TOOL_ROUNDS", 3))
    # The fact-checker's job is mechanical (rewrite maths for sympy, search notes): low
    # reasoning effort cuts its tokens and latency. "" = provider default.
    FACT_CHECK_REASONING_EFFORT: str = os.getenv("FACT_CHECK_REASONING_EFFORT", "low")
    # Attempts per LLM call (retries rate limits and transient errors with backoff)
    LLM_MAX_ATTEMPTS: int = int(os.getenv("LLM_MAX_ATTEMPTS", 3))
    # Prompt-injection gatekeeper. GATEKEEPER_MODEL is the precision-first classifier that may block.
    # "always" classifies every answer; "regex" only when the regex pre-filter fires
    # (the regex alone caught 3 of 18 attacks in the golden-set benchmark).
    GATEKEEPER_MODE: str = os.getenv("GATEKEEPER_MODE", "always")
    # Score threshold for Prompt Guard models (they return a 0-1 injection probability)
    GATEKEEPER_THRESHOLD: float = float(os.getenv("GATEKEEPER_THRESHOLD", 0.5))
    # Recall-first classifier whose flags route an answer to the critic and to teacher
    # review without blocking it. Empty string disables it.
    GATEKEEPER_AUDIT_MODEL: str = os.getenv("GATEKEEPER_AUDIT_MODEL", "openai/gpt-oss-20b")
    # Output cap for JSON-verdict classifiers (reasoning + verdict use ~150-400 tokens)
    GATEKEEPER_MAX_TOKENS: int = int(os.getenv("GATEKEEPER_MAX_TOKENS", 1000))

    # ── MCP server ────────────────────────────────────────────────
    # Public base URL of this API; the MCP endpoint is {PUBLIC_API_URL}/mcp
    PUBLIC_API_URL: str = os.getenv("PUBLIC_API_URL", "http://localhost:8000")
    # Extra Host headers accepted by the MCP endpoint (DNS-rebinding protection),
    # comma-separated, e.g. "192.168.1.20:8000". Localhost and PUBLIC_API_URL are always allowed.
    MCP_ALLOWED_HOSTS: str = os.getenv("MCP_ALLOWED_HOSTS", "")

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()
    