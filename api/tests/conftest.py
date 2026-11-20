import os

# Must run before `app` is imported: settings are read at import time.
# Environment variables take precedence over api/.env, so tests stay hermetic even
# when a developer's .env holds real credentials: an unmocked LLM call fails loudly
# instead of spending API credits, and nothing can reach a real database.
os.environ["SECRET_KEY"] = "pytest-only-signing-key-0123456789abcdef"
os.environ["GROQ_API_KEY"] = ""
os.environ["DATABASE_URL"] = "mongodb://127.0.0.1:9/pytest-must-not-connect"
os.environ["GRADING_PIPELINE"] = "graph"
os.environ["PUBLIC_API_URL"] = "http://localhost:8000"
# Graph tests script a JSON-verdict gatekeeper; Prompt Guard paths have their own tests
os.environ["GATEKEEPER_MODEL"] = "openai/gpt-oss-20b"
os.environ["GATEKEEPER_MODE"] = "regex"
os.environ["GATEKEEPER_AUDIT_MODEL"] = ""

import pytest  # noqa: E402


@pytest.fixture
def fake_repo(monkeypatch):
    from tests.fakes import FakeRepo
    return FakeRepo().install(monkeypatch)
