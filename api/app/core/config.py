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
    GATEKEEPER_MODEL: str = os.getenv("GATEKEEPER_MODEL", "llama-3.1-8b-instant")
    # Below this grader confidence, the Critic/Auditor agent reviews the draft
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", 0.85))
    # Scores within ±0.5 of this mark are "borderline" and always audited
    PASS_MARK: float = float(os.getenv("PASS_MARK", 5.0))
    MAX_TOOL_ROUNDS: int = int(os.getenv("MAX_TOOL_ROUNDS", 3))

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()
    