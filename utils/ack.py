# coding: utf-8
"""Stale-tolerant callback acknowledgement (redesign §16).

Telegram accepts answerCallbackQuery only within ~10 s of the tap. A
tap whose update sits behind a long-running handler (PTB processes
updates sequentially) — or behind a process stall — is delivered after
the window and cannot be answered: the API raises BadRequest ("Query
is too old and response timeout expired or query id is invalid").

A too-old query is a property of the tap's age, not a failure of the
action: the message and its buttons are still there, so the intent is
still valid. The action proceeds; only the impossible toast is
skipped. Surfacing it as the generic unexpected-error card is exactly
what §16 forbids (incident 2026-10-28 21:40: two stale 📥 Tasks taps
behind an 88 s download each produced a "Something went wrong" card).
"""
from logging import getLogger

from telegram.error import BadRequest, TelegramError

logger = getLogger(__name__)


async def answer_callback(cq, text: str | None = None) -> bool:
    """Answer a callback query, tolerating a stale (too-old) query.

    Returns True when the answer went through. A stale or failed
    answer never raises: the caller proceeds with its action (the ack
    is best-effort; the action is what the user came for).
    """
    try:
        if text:
            await cq.answer(text)
        else:
            await cq.answer()
        return True
    except BadRequest:
        # "Query is too old and response timeout expired or query id
        # is invalid" — the tap outlived Telegram's answer window.
        logger.info(
            "callback query too old to answer (stale tap); "
            "proceeding with the action"
        )
        return False
    except TelegramError:
        # any other failure to ack (network hiccup) is cosmetic: the
        # action still runs
        logger.warning("could not answer the callback query", exc_info=True)
        return False
