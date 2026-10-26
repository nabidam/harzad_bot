"""#10 utils.decorators — restricted / sync_user / log_message / send_action.

sync_user and log_message run against the live test DB (unique tg_ids,
cleaned up in teardown).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import cruds.log as log_cruds
import cruds.user as user_cruds
from connectors.db import SessionLocal
from models.log import Log
from models.user import User
from utils.decorators import log_message, restricted, send_action, sync_user
from configurations.settings import ADMINS

ADMIN_ID = int(ADMINS[0])


def _fresh_tg_id() -> int:
    # tg_id is a 32-bit int in the schema: keep inside INT4 range (2.147e9)
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


def make_update(text="hi", tg_id=12345):
    update = MagicMock()
    update.message.text = text
    update.message.chat_id = 1
    update.effective_message.chat_id = 1
    update.effective_chat.id = 1
    update.effective_user = MagicMock(
        id=tg_id, first_name="F", last_name="L", username="u"
    )
    return update


def make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


@pytest.fixture
def db_session():
    from connectors.db import get_db

    gen = get_db()
    db = next(gen)
    yield db
    db.close()


@pytest.fixture
def test_tg():
    tg_id = _fresh_tg_id()
    yield tg_id
    db = SessionLocal()
    db.query(Log).filter(Log.tg_id == tg_id).delete()
    db.query(User).filter(User.tg_id == tg_id).delete()
    db.commit()
    db.close()


# --- restricted -------------------------------------------------------------

async def test_restricted_allows_admin():
    calls = []

    @restricted
    async def func(update, context):
        calls.append(1)

    await func(make_update(tg_id=ADMIN_ID), make_context())
    assert calls == [1]


async def test_restricted_denies_non_admin():
    calls = []

    @restricted
    async def func(update, context):
        calls.append(1)

    result = await func(make_update(tg_id=ADMIN_ID + 1), make_context())
    assert result is None
    assert calls == []


# --- sync_user ----------------------------------------------------------------

async def test_sync_user_creates_then_updates(db_session, test_tg):
    seen = []

    @sync_user
    async def func(update, context):
        seen.append(update.message.text)
        return "ok"

    update = make_update(tg_id=test_tg)
    assert await func(update, make_context()) == "ok"
    assert user_cruds.user_exists(db_session, tg_id=test_tg)
    row = user_cruds.get_user(db_session, tg_id=test_tg)
    assert row.username == "u"

    # second call hits the update path
    assert await func(update, make_context()) == "ok"
    assert seen == ["hi", "hi"]
    assert user_cruds.user_exists(db_session, tg_id=test_tg)


async def test_sync_user_reraises_db_failure(db_session, test_tg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(user_cruds, "create_user", boom)

    @sync_user
    async def func(update, context):
        return "never"

    with pytest.raises(RuntimeError, match="db down"):
        await func(make_update(tg_id=test_tg), make_context())


# --- log_message ----------------------------------------------------------------

async def test_log_message_writes_row(db_session, test_tg):
    @log_message
    async def func(update, context):
        return "ran"

    assert await func(make_update(text="logged line", tg_id=test_tg), make_context()) == "ran"
    logs = db_session.query(Log).filter(Log.tg_id == test_tg).all()
    assert len(logs) == 1
    assert logs[0].message == "logged line"


async def test_log_message_swallows_db_failure(db_session, test_tg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(log_cruds, "create_log", boom)

    @log_message
    async def func(update, context):
        return "still ran"

    # Unlike sync_user, log failure must not break the handler.
    assert await func(make_update(tg_id=test_tg), make_context()) == "still ran"


# --- send_action ------------------------------------------------------------------

async def test_send_action_sends_then_runs():
    calls = []

    @send_action("typing")
    async def func(update, context):
        calls.append("done")

    context = make_context()
    await func(make_update(), context)
    context.bot.send_chat_action.assert_awaited_once()
    kwargs = context.bot.send_chat_action.await_args.kwargs
    assert kwargs["chat_id"] == 1
    assert kwargs["action"] == "typing"
    assert calls == ["done"]


# --- composition (the stack the handlers actually use) -----------------------------

async def test_composed_stack_order(db_session, test_tg):
    order = []

    @send_action("upload_video")
    @sync_user
    @log_message
    async def func(update, context):
        order.append("func")
        return "composed"

    context = make_context()
    result = await func(make_update(tg_id=test_tg), context)
    assert result == "composed"
    assert order == ["func"]
    context.bot.send_chat_action.assert_awaited_once()
    assert user_cruds.user_exists(db_session, tg_id=test_tg)
    assert db_session.query(Log).filter(Log.tg_id == test_tg).count() == 1
