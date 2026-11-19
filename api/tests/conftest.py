import os

# Must run before `app` is imported: settings are read at import time.
os.environ.setdefault("SECRET_KEY", "pytest-only-signing-key-0123456789abcdef")

import pytest  # noqa: E402


@pytest.fixture
def fake_repo(monkeypatch):
    from tests.fakes import FakeRepo
    return FakeRepo().install(monkeypatch)
