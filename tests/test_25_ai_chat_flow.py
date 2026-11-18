"""#25 AI chat flow (redesign §9, §4.1, §4.3, §7.3).

Pins the reworked core: the request is a task (registered up front,
cancellable via task:cancel:<token>), one status message on the
shared status engine with the AI thinking lines, the 30 s answer
guard (compressed in tests) with the timeout card + Retry, honest
cancel (a post-cancel answer is discarded, never sent), the LLM
failure card, and retry replay of the stored question under a new
task token.

FakeLLM stands in for langchain's ChatOpenAI.ainvoke (async — the
real call runs on the loop, like production).
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.handlers.ai.chat as ai_chat
import bot.handlers.tasks as tasks
from utils import copy
from utils.tasks import CANCELLED, DONE, FAILED, get_registry

OWNER_ID = 9_000_000_004


def make_update(text, tg_id=OWNER_ID, chat_id=7):
    update = MagicMock()
    msg = MagicMock(chat_id=chat_id, message_id=100, text=text,
                    reply_text=AsyncMock())
    update.message = msg
    update.effective_message = msg
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    update.callback_query = None
    return update


def make_callback(data, tg_id=OWNER_ID, chat_id=7):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    cq.message = MagicMock(chat_id=chat_id, message_id=600,
                           reply_text=AsyncMock())
    update.callback_query = cq
    update.effective_message = cq.message
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    # ONE edit mock shared by the status message (flow edits) and the
    # bot (status-engine threshold edits): the tests read the full
    # status timeline from a single, correctly ordered record.
    edit = AsyncMock()
    status = MagicMock()
    status.message_id = 500
    status.delete = AsyncMock()
    status.edit_text = edit
    context.status_message = status
    context.bot = MagicMock()
    for m in ("send_chat_action", "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    context.bot.edit_message_text = edit
    context.bot.send_message = AsyncMock(return_value=status)
    return context


class FakeLLM:
    """Stands in for ChatOpenAI.ainvoke (async, loop-side, like the
    real call). `gate` blocks the answer until the test releases it;
    `slow` sleeps; `fail` raises."""

    def __init__(self, content="hi there", *, slow=0.0, fail=False,
                 gate=None):
        self.content = content
        self.slow = slow
        self.fail = fail
        self.gate = gate
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if self.gate is not None:
            await self.gate.wait()
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.fail:
            raise RuntimeError("fake LLM failure")
        return SimpleNamespace(content=self.content)


async def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


def _edits(context):
    return [c.kwargs.get("text") or "" for c in
            context.bot.edit_message_text.await_args_list]


def _status_token(context):
    row = context.bot.send_message.await_args_list[0].kwargs[
        "reply_markup"].inline_keyboard[0]
    return row[0].callback_data.removeprefix("task:cancel:")


async def test_ai_answer_full_flow(monkeypatch):
    """Spec §9: accepted status with [⏹ Stop][🏠 Home], the LLM gets
    the system prompt + the question, the answer goes out as a NEW
    message (escaped, HTML) with [Ask another question][🏠 Home], the
    status is removed, task DONE."""
    fake = FakeLLM(content="Hello <b>there</b>")
    monkeypatch.setattr(ai_chat, "llm", fake)
    update = make_update("ping")
    context = make_context()

    await ai_chat.run_ai_chat(update, context, "ping")

    assert fake.calls[0][0] == ("system", ai_chat.SYSTEM_PROMPT)
    assert fake.calls[0][1] == ("user", "ping")

    first = context.bot.send_message.await_args_list[0].kwargs
    assert first["text"] == copy.AI_THINKING
    row = first["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert row[0].text == copy.BTN_AI_STOP
    assert row[1].callback_data == "nav:home"

    context.status_message.delete.assert_awaited()
    assert context.bot.send_message.await_count == 2
    last = context.bot.send_message.await_args_list[1].kwargs
    assert last["text"] == "Hello &lt;b&gt;there&lt;/b&gt;"
    assert last["parse_mode"] == "HTML"
    arow = last["reply_markup"].inline_keyboard[0]
    assert arow[0].text == copy.BTN_AI_ASK_AGAIN
    assert [b.callback_data for b in arow] == ["nav:ai:chat", "nav:home"]

    assert get_registry(context).get(token).status == DONE


async def test_ai_stage_lines_follow_the_thresholds(monkeypatch):
    """Spec §9: at 5 s the status reads the working line, at 15 s the
    slow line — the ticker floors elapsed to whole seconds, so the
    thresholds are integers (compressed for the test)."""
    fake = FakeLLM(slow=2.5)
    monkeypatch.setattr(ai_chat, "llm", fake)
    monkeypatch.setattr(ai_chat, "WORKING_AT_SECONDS", 1)
    monkeypatch.setattr(ai_chat, "SLOW_AT_SECONDS", 2)
    update = make_update("ping")
    context = make_context()

    await ai_chat.run_ai_chat(update, context, "ping")

    texts = _edits(context)
    i_working = texts.index(copy.AI_WORKING.format(elapsed=1))
    i_slow = texts.index(copy.AI_SLOW.format(elapsed=2))
    assert i_working < i_slow


async def test_ai_timeout_guard(monkeypatch):
    """The 30 s answer guard (spec §9, compressed) aborts the
    in-flight request and ends in the timeout card with
    [Retry][Home] — never a claim the backend stopped; no answer is
    sent."""
    monkeypatch.setattr(ai_chat, "ANSWER_TIMEOUT_SECONDS", 0.3)
    fake = FakeLLM(slow=1.0)
    monkeypatch.setattr(ai_chat, "llm", fake)
    update = make_update("ping")
    context = make_context()

    await ai_chat.run_ai_chat(update, context, "ping")

    assert context.bot.send_message.await_count == 1  # status only
    context.status_message.delete.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_AI_TIMEOUT
    token = _status_token(context)
    row = last["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert task.note == "timed_out"


async def test_ai_cancel_discards_late_answer(monkeypatch):
    """Spec §4.3/§9: Stop shows the honest 'requested' line, aborts
    the in-flight request, and an answer that lands after the stop is
    discarded — never sent; task CANCELLED."""
    release = asyncio.Event()
    fake = FakeLLM(content="late answer", gate=release)
    monkeypatch.setattr(ai_chat, "llm", fake)
    update = make_update("ping")
    context = make_context()

    run = asyncio.create_task(ai_chat.run_ai_chat(update, context, "ping"))
    registry = get_registry(context)
    await _wait_for(lambda: context.bot.send_message.await_count >= 1)
    task = registry.all_active()[0]
    tasks._cancel_task(task, update, context)
    await run
    release.set()  # the "late" answer now completes — must be discarded

    assert context.bot.send_message.await_count == 1  # no answer out
    context.status_message.delete.assert_not_awaited()
    assert copy.TASK_CANCEL_REQUESTED in _edits(context)
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_AI_CANCELLED
    row = last["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == ["nav:home", "nav:tasks"]
    assert registry.get(task.token).status == CANCELLED


async def test_ai_llm_error_unexpected_card(monkeypatch):
    """A failed LLM request is an internal error (spec §7.1 class 6):
    the unexpected card with [Retry][Home], task FAILED — the question
    is stored, so Retry is safe."""
    fake = FakeLLM(fail=True)
    monkeypatch.setattr(ai_chat, "llm", fake)
    update = make_update("ping")
    context = make_context()

    await ai_chat.run_ai_chat(update, context, "ping")

    assert context.bot.send_message.await_count == 1
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_UNEXPECTED
    token = _status_token(context)
    row = last["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert task.note == "llm error"


async def test_ai_empty_completion_is_error(monkeypatch):
    """An empty completion is a backend failure, not an answer: the
    unexpected card with [Retry][Home], no empty message sent."""
    monkeypatch.setattr(ai_chat, "llm", FakeLLM(content="   "))
    update = make_update("ping")
    context = make_context()

    await ai_chat.run_ai_chat(update, context, "ping")

    assert context.bot.send_message.await_count == 1  # status only
    context.status_message.delete.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_UNEXPECTED
    token = _status_token(context)
    row = last["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_ai_retry_replays_question(monkeypatch):
    """Spec §7.3: the failure card's Retry re-runs the stored question
    under a NEW task token — the original stays terminal, and the
    replay (kind="ai") gets the healthy backend."""
    monkeypatch.setattr(ai_chat, "llm", FakeLLM(fail=True))
    update = make_update("ping")
    context = make_context()
    await ai_chat.run_ai_chat(update, context, "ping")
    token = _status_token(context)
    assert get_registry(context).get(token).status == FAILED

    monkeypatch.setattr(ai_chat, "llm", FakeLLM(content="ok"))
    await tasks.on_task_retry(make_callback(f"task:retry:{token}"), context)

    last = context.bot.send_message.await_args_list[-1].kwargs
    assert last["text"] == "ok"
    registry = get_registry(context)
    assert registry.get(token).status == FAILED  # the original stays terminal
    replayed = [t for t in registry._tasks.values()
                if t.status == DONE and t.kind == "ai"]
    assert len(replayed) == 1
    assert replayed[0].input == "ping"


async def test_ai_long_response_is_truncated(monkeypatch):
    """An answer exceeding Telegram's 4096-char limit is safely truncated."""
    long_content = "A" * 5000
    monkeypatch.setattr(ai_chat, "llm", FakeLLM(content=long_content))
    update = make_update("essay")
    context = make_context()

    await ai_chat.run_ai_chat(update, context, "essay")

    last = context.bot.send_message.await_args_list[-1].kwargs
    assert len(last["text"]) <= 4000
    assert "<i>(truncated)</i>" in last["text"]
    assert last["parse_mode"] == "HTML"
    token = _status_token(context)
    assert get_registry(context).get(token).status == DONE
