import os

# Must run before `app` is imported: settings are read at import time.
os.environ.setdefault("SECRET_KEY", "pytest-only-signing-key-0123456789abcdef")
