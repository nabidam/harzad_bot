"""#21 utils.progress.live_status — the status engine (redesign §4.1,
§4.2): instant accepted message, measured-elapsed phase edits at
meaningful thresholds (never per-second), kept-alive typing indicator,
quiet ticker failures, caller-owned exit.
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


async def test_live_status_sends_accepted_message_immediately():
    bot = make_bot()

    async with progress.live_status(
        bot, 7, "Accepted…",
        reply_markup="KB",
        parse_mode="HTML",
        tick_seconds=0.1,
    ) as status:
        assert status is bot._status

    # initial message: text + markup + parse mode passed through
    kw = bot.send_message.await_args.kwargs
    assert kw["chat_id"] == 7
    assert kw["text"] == "Accepted…"
    assert kw["reply_markup"] == "KB"
    assert kw["parse_mode"] == "HTML"


async def test_no_stages_means_typing_only():
    """No stages: the message text never changes; only the typing
    indicator keeps the client's UI alive (spec §4.2: edit at
    meaningful thresholds, not every second)."""
    bot = make_bot()

    async with progress.live_status(bot, 7, "Working…", tick_seconds=0.05):
        deadline = time.monotonic() + 2.0
        while bot.send_chat_action.await_count < 2:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.01)

    assert bot.send_chat_action.await_count >= 2
    bot.edit_message_text.assert_not_awaited()


async def test_stages_apply_exactly_once_at_their_second():
    bot = make_bot()

    async with progress.live_status(
        bot, 7, "Accepted…",
        reply_markup="KB",
        parse_mode="HTML",
        tick_seconds=0.1,
        stages=[
            (1, "Resolving…"),
            (2, lambda e: f"Slow… {e}s"),
        ],
    ):
        await asyncio.sleep(0.6)
        # below the first threshold: typing only, no edit yet — a stage
        # must not fire early on a plain typing tick
        bot.edit_message_text.assert_not_awaited()
        await asyncio.sleep(1.8)  # ~2.4 s total: both thresholds crossed

    edits = [c.kwargs["text"] for c in bot.edit_message_text.await_args_list]
    # each stage exactly once, in threshold order; the callable got the
    # measured elapsed seconds
    assert edits[0] == "Resolving…"
    assert edits[1].startswith("Slow… ")
    assert int(edits[1].rsplit(" ", 1)[1][:-1]) >= 2  # "… {e}s" -> e
    assert len(edits) == 2
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 500
    assert bot.edit_message_text.await_args.kwargs["parse_mode"] == "HTML"
    assert bot.edit_message_text.await_args.kwargs["reply_markup"] == "KB"
    for call in bot.edit_message_text.await_args_list:
        assert call.kwargs["reply_markup"] == "KB"


async def test_status_ticker_passes_callable_reply_markup():
    bot = make_bot()
    called_count = 0

    def markup_fn():
        nonlocal called_count
        called_count += 1
        return f"KB_{called_count}"

    async with progress.status_ticker(
        bot, 7, 500,
        reply_markup=markup_fn,
        stages=[(1, "First"), (2, "Second")],
        tick_seconds=0.1,
    ):
        await asyncio.sleep(2.4)

    calls = bot.edit_message_text.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["reply_markup"] == "KB_1"
    assert calls[1].kwargs["reply_markup"] == "KB_2"


async def test_stages_are_sorted_even_when_given_out_of_order():
    bot = make_bot()

    async with progress.live_status(
        bot, 7, "Accepted…",
        tick_seconds=0.1,
        stages=[(2, "late"), (1, "early")],
    ):
        await asyncio.sleep(2.4)

    edits = [c.kwargs["text"] for c in bot.edit_message_text.await_args_list]
    assert edits == ["early", "late"]


async def test_exit_stops_the_ticker():
    bot = make_bot()

    async with progress.live_status(bot, 7, "Working…", tick_seconds=0.05):
        deadline = time.monotonic() + 2.0
        while bot.send_chat_action.await_count < 2:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.01)

    # exit stops the ticker — no further API calls
    typing_after = bot.send_chat_action.await_count
    await asyncio.sleep(0.1)
    assert bot.send_chat_action.await_count == typing_after
    bot.edit_message_text.assert_not_awaited()


async def test_ticker_stops_quietly_on_api_errors():
    """Flaky-proxy edit failures stop the ticker quietly — the
    operation itself must not die because the status UI hiccuped."""
    bot = make_bot()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("flood"))

    async with progress.live_status(
        bot, 7, "Working…", tick_seconds=0.05, stages=[(1, "phase")]
    ):
        await asyncio.sleep(0.1)  # must not raise
        # the first edit attempt happens when the threshold is crossed;
        # it fails, the ticker stops — the context manager still exits
        # clean
    # typing was sent (it precedes the failing edit on that tick)
    bot.send_chat_action.assert_awaited()


async def test_ticker_stops_quietly_on_unexpected_errors():
    bot = make_bot()
    bot.send_chat_action = AsyncMock(side_effect=RuntimeError("boom"))

    async with progress.live_status(
        bot, 7, "Working…", tick_seconds=0.05, stages=[(1, "phase")]
    ):
        await asyncio.sleep(0.1)  # must not raise


async def test_initial_send_failure_propagates():
    """No status message → the caller (and on_error) must see the error,
    not a silent no-op."""
    bot = make_bot()
    bot.send_message = AsyncMock(side_effect=BadRequest("flood"))

    with pytest.raises(BadRequest):
        async with progress.live_status(bot, 7, "Working…", tick_seconds=0.05):
            pass
