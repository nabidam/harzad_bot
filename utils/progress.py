"""Live status feedback for slow operations (search UX, 2026-10-27).

Telegram gives a slow operation no native progress UI: the typing
indicator dies after ~5 s and then the user just stares at their own
message for the rest of the wait. `live_status` owns one in-place status
message for the whole operation:

- sends the status text immediately (before the slow call starts)
- re-sends the typing indicator and re-renders the text with an
  elapsed counter (via `tick_text(seconds)`) every few seconds, so a
  45 s wait reads as "working", not "frozen"
- never touches the message on exit: the caller deletes it (cards or
  audio already sent) or edits it into the final answer

Ticker API failures (flaky proxy, user deleted the message) stop the
ticker quietly — they never break the operation itself.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from logging import getLogger

from telegram.error import TelegramError
from telegram.constants import ChatAction

logger = getLogger(__name__)

# Telegram's typing indicator lasts ~5 s; refresh just under that.
TYPING_REFRESH_SECONDS = 4.0


@asynccontextmanager
async def live_status(bot, chat_id, text, *, reply_markup=None, parse_mode=None,
                      tick_seconds=TYPING_REFRESH_SECONDS, tick_text=None):
    """Keep a status message alive while the body runs.

    Yields the sent `Message`. On exit the ticker is stopped and joined;
    the message itself is left for the caller to finish (edit/delete).
    """
    message = await bot.send_message(
        chat_id=chat_id, text=text, reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    stop = asyncio.Event()

    async def _ticker():
        started = time.monotonic()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            elapsed = max(1, int(time.monotonic() - started))
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                if tick_text is not None:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message.message_id,
                        text=tick_text(elapsed),
                        parse_mode=parse_mode,
                    )
            except TelegramError:
                logger.debug("live_status: ticker stopped (Bot API error)", exc_info=True)
                return
            except Exception:
                logger.warning("live_status: ticker stopped (unexpected error)", exc_info=True)
                return

    task = asyncio.create_task(_ticker())
    try:
        yield message
    finally:
        stop.set()
        await asyncio.gather(task, return_exceptions=True)
