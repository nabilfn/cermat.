"""Test configuration. Runs before any app module is imported.

Integration tests use a *separate* database (``<name>_test``) on the same server.
The harness refuses to reset any database whose name does not end in ``_test``.
"""

from __future__ import annotations

import os

base = os.environ.get("DATABASE_URL", "postgresql+asyncpg://cermat:cermat@db:5432/cermat")
if not base.rsplit("/", 1)[-1].endswith("_test"):
    base = base.rsplit("/", 1)[0] + "/" + base.rsplit("/", 1)[-1] + "_test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", base)
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-000000000000"
os.environ["DATA_DIR"] = "/tmp/cermat-test-uploads"
os.environ["STORAGE_PROVIDER"] = "local"
os.environ["OPENAI_API_KEY"] = ""  # tests never call a real AI provider
os.environ["RATE_LIMIT_AI_PER_MINUTE"] = "1000"
