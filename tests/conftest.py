"""Shared fixtures for the harzad_bot test suite.

Imported by pytest before any test module, so the environment pins below
apply before the first `import configurations.settings`. `load_dotenv()`
never overrides an env var that is already set, which is what makes the
DB pin stick.
"""
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

# --- env pins (must happen before app imports) ---------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Tests talk to the local test Postgres container, never the .env Supabase
# stack (the bot is launched with the same explicit DB_URI).
os.environ.setdefault(
    "DB_URI", "postgresql+psycopg2://postgres:hz9x@127.0.0.1:5437/harzad"
)


def make_init_data(params: dict, token: str | None = None) -> str:
    """Build a Telegram WebApp initData blob (independent re-implementation).

    Mirrors the scheme in the Web Apps reference: data_check_string is the
    k=v lines of every param (sorted, `hash` excluded) joined by newlines;
    the hash is HMAC-SHA256 keyed with HMAC-SHA256(b"WebAppData", token).
    """
    from configurations.settings import TOKEN as _TOKEN

    token = token or _TOKEN
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    all_params = {**params, "hash": digest}
    from urllib.parse import urlencode

    return urlencode(all_params)


def user_params(user_id: int = 777000111, age_days: float = 0.0) -> dict:
    """A plausible initData user payload (top-level auth_date style)."""
    stamp = int(time.time() - age_days * 86400)
    user = {
        "id": user_id,
        "first_name": "Test",
        "last_name": "User",
        "username": "testuser",
        "language_code": "en",
        "is_premium": False,
    }
    return {"user": json.dumps(user), "auth_date": str(stamp)}


# --- aiohttp mini app -----------------------------------------------------

import pytest
from aiohttp.test_utils import TestClient, TestServer

import web.app as webapp


@pytest.fixture
def fake_llm(monkeypatch):
    """A fake ChatOpenAI for the web chat endpoint (no network)."""
    from bot.handlers.ai import chat as chat_handler

    class _LLM:
        async def ainvoke(self, messages):
            self.last_messages = list(messages)

            class _Resp:
                content = "test reply"

            return _Resp()

    llm = _LLM()
    monkeypatch.setattr(chat_handler, "llm", llm)
    return llm


@pytest.fixture
def web_app(monkeypatch, fake_llm, tmp_path):
    """A mini-app web.Application with a stubbed downloader and fake bot.

    `app["_test_file_size"]` (bytes, default 1024) controls the size of the
    fake download — set it before creating a job to exercise the size
    branches (e.g. the 50 MB chat-delivery cap).
    """
    from unittest.mock import AsyncMock

    fake_bot = AsyncMock()
    app = webapp.create_app(fake_bot, base_url="https://app.test")
    app["bot"] = fake_bot
    app["_test_file_path"] = str(tmp_path / "fake_video.mp4")
    app["_test_file_size"] = 1024

    def fake_ytdlp_run(url):  # sync: run_job wraps it in asyncio.to_thread
        path = app["_test_file_path"]
        with open(path, "wb") as f:
            f.write(b"0" * app["_test_file_size"])
        return (
            {"title": "Test Video", "duration": 42, "thumbnail": "http://t/x.jpg"},
            path,
        )

    # run_job resolves the module-global _ytdlp_run at call time.
    monkeypatch.setattr(webapp, "_ytdlp_run", fake_ytdlp_run)
    return app, fake_bot
