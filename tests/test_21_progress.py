"""#21 utils.progress.live_status — live feedback for slow operations
(2026-10-27 search UX): instant status message, kept-alive typing
indicator, elapsed counter, quiet ticker failures, caller-owned exit.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from utils import progress


def make_bot():
    bot = MagicMock()
    status = MagicMock()
    status.message_id = 500
    bot.send_message = AsyncMock(return_value=status)
    bot.send_chat_action = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot._status = status
    return bot


async def test_live_status_sends_message_and_ticks():
    bot = make_bot()

    async with progress.live_status(
        bot, 7, "Searching…",
        tick_seconds=0.05,
        tick_text=lambda e: f"… {e}s",
        reply_markup="KB",
        parse_mode="HTML",
    ) as status:
        assert status is bot._status
        await asyncio.sleep(0.15)  # ~3 ticks

    # initial message: text + markup + parse mode passed through
    kw = bot.send_message.await_args.kwargs
    assert kw["chat_id"] == 7
    assert kw["text"] == "Searching…"
    assert kw["reply_markup"] == "KB"
    assert kw["parse_mode"] == "HTML"
    # ticker kept the typing indicator alive and re-rendered the text
    assert bot.send_chat_action.await_count >= 2
    assert bot.edit_message_text.await_count >= 2
    texts = [c.kwargs["text"] for c in bot.edit_message_text.await_args_list]
    # sub-second ticks all floor to the first second
    assert texts == ["… 1s"] * len(texts)
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 500
    assert bot.edit_message_text.await_args.kwargs["parse_mode"] == "HTML"

    # exit stops the ticker — no further API calls
    typing_after = bot.send_chat_action.await_count
    edit_after = bot.edit_message_text.await_count
    await asyncio.sleep(0.1)
    assert bot.send_chat_action.await_count == typing_after
    assert bot.edit_message_text.await_count == edit_after


async def test_live_status_without_tick_text_only_types():
    bot = make_bot()

    async with progress.live_status(bot, 7, "Working…", tick_seconds=0.05):
        deadline = time.monotonic() + 2.0
        while bot.send_chat_action.await_count < 2:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.01)

    assert bot.send_chat_action.await_count >= 2
    bot.edit_message_text.assert_not_awaited()


async def test_live_status_ticker_survives_api_errors():
    """Flaky-proxy edits/typing failures stop the ticker quietly — the
    operation itself must not die because the status UI hiccuped."""
    bot = make_bot()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("flood"))

    async with progress.live_status(
        bot, 7, "Working…", tick_seconds=0.05,
        tick_text=lambda e: f"{e}s",
    ):
        await asyncio.sleep(0.1)  # first tick blows up; CM still exits clean

    bot.send_chat_action.assert_awaited()


async def test_live_status_ticker_survives_unexpected_errors():
    bot = make_bot()
    bot.send_chat_action = AsyncMock(side_effect=RuntimeError("boom"))

    async with progress.live_status(
        bot, 7, "Working…", tick_seconds=0.05, tick_text=lambda e: f"{e}s"
    ):
        await asyncio.sleep(0.1)  # must not raise


async def test_live_status_initial_send_failure_propagates():
    """No status message → the caller (and on_error) must see the error,
    not a silent no-op."""
    bot = make_bot()
    bot.send_message = AsyncMock(side_effect=BadRequest("flood"))

    with pytest.raises(BadRequest):
        async with progress.live_status(bot, 7, "Working…", tick_seconds=0.05):
            pass
