"""Status engine for slow operations (UX redesign §4.1, §4.2).

One bot message per task, edited in place at meaningful thresholds —
never every second:

- t=0    the accepted message (what is happening, plus context)
- t=5s   still resolving, with the measured elapsed seconds
- t=30s  "taking longer than usual"
- t=60s  "still working — no reliable finish time available yet"

The caller supplies the stage texts (the per-platform copy from
utils/copy.py); a phase the engine cannot truthfully show is simply
omitted — the engine never invents sub-phases, and elapsed time is
measured, not simulated. The typing indicator is refreshed every ~4 s:
a chat action, not a message edit, so it keeps the client's UI alive
without touching the message.

Two entry points:

- live_status: sends the t=0 accepted message, then keeps it alive;
  yields the Message.
- status_ticker: the same ticker for a message that already exists
  (flows that own the message and edit it at their own phase changes).

Ticker API failures (flaky proxy, user deleted the message) stop the
ticker quietly — they never break the operation itself. The message is
left to the caller (edit into the final answer or delete).
"""
import asyncio
import time
from contextlib import asynccontextmanager
from logging import getLogger
from typing import Callable, Sequence, Tuple, Union

from telegram.constants import ChatAction
from telegram.error import TelegramError

logger = getLogger(__name__)

# Telegram's typing indicator lasts ~5 s; refresh just under that.
TYPING_REFRESH_SECONDS = 4.0

# Spec §4.2 standard progression thresholds (seconds).
STAGE_RESOLVING = 5
STAGE_SLOW = 30
STAGE_STILL_WORKING = 60

# (seconds, replacement) — replacement is a string or a callable
# receiving the measured elapsed seconds and returning the new text.
Stage = Tuple[int, Union[str, Callable[[int], str]]]


@asynccontextmanager
async def status_ticker(
    bot, chat_id, message_id, *, stages: Sequence[Stage] = (),
    reply_markup=None,
    parse_mode: str | None = None,
    tick_seconds: float = TYPING_REFRESH_SECONDS,
):
    """Keep an EXISTING status message alive (spec §4.2).

    `stages` is an ascending list of (at_seconds, replacement); each
    stage is applied exactly once when the measured elapsed time
    reaches the threshold. Elapsed time is measured (monotonic), never
    simulated. `parse_mode` is applied to stage edits like the initial
    send (the search stage texts are HTML). The typing indicator is
    refreshed on its own ~4 s cadence — a chat action, not an edit.

    Yields nothing; the caller owns the message. Ticker API failures
    stop the ticker quietly.
    """
    stop = asyncio.Event()
    started = time.monotonic()
    stages = sorted(stages, key=lambda stage: stage[0])
    next_stage = 0

    async def _ticker():
        nonlocal next_stage
        while not stop.is_set():
            # Sleep until whichever comes first: the typing refresh or
            # the next threshold, so stages fire at their second, not
            # up to a full tick late.
            wait_for = tick_seconds
            if next_stage < len(stages):
                deadline = stages[next_stage][0] - (time.monotonic() - started)
                if deadline > 0:
                    wait_for = min(wait_for, deadline)
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait_for)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            # Measured, floored — never padded (a 0.4 s old operation is
            # not "1s elapsed"): the threshold test must see real time.
            elapsed = int(time.monotonic() - started)
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                # Apply every threshold crossed since the last tick.
                while next_stage < len(stages) and elapsed >= stages[next_stage][0]:
                    replacement = stages[next_stage][1]
                    new_text = (
                        replacement(elapsed)
                        if callable(replacement)
                        else replacement
                    )
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=new_text,
                        reply_markup=(
                            reply_markup()
                            if callable(reply_markup)
                            else reply_markup
                        ),
                        parse_mode=parse_mode,
                    )
                    next_stage += 1
            except TelegramError:
                logger.debug("status ticker stopped (Bot API error)", exc_info=True)
                return
            except Exception:
                logger.warning("status ticker stopped (unexpected error)",
                               exc_info=True)
                return

    task = asyncio.create_task(_ticker())
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def live_status(bot, chat_id, text, *, reply_markup=None, parse_mode=None,
                      stages: Sequence[Stage] = (),
                      tick_seconds: float = TYPING_REFRESH_SECONDS):
    """Send the t=0 accepted message and keep it alive while the body
    runs.

    `text` is the t=0 accepted message; `stages` as in status_ticker.
    Yields the sent `Message`. On exit the ticker is stopped and joined;
    the message itself is left to the caller to finish (edit/delete).
    """
    message = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=(
            reply_markup()
            if callable(reply_markup)
            else reply_markup
        ),
        parse_mode=parse_mode,
    )
    async with status_ticker(
        bot, chat_id, message.message_id,
        stages=stages, reply_markup=reply_markup, parse_mode=parse_mode,
        tick_seconds=tick_seconds,
    ):
        yield message
