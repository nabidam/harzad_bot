"""The cloud re-send ladder core (plan §3) — shared by the chat handler
(`bot/handlers/music/library.py`) and the Mini App API (`web/app.py`,
plan §7/§12.14).

Steps 1-2 are ladder mechanics only: sendAudio by the stored `file_id`
(Telegram relays the stored file server-side), then copyMessage of the
original delivery. Step 3 (a full re-download) and the exhausted state
are owned by the CALLER, because each surface renders its own failure
and drives a different step-3 engine: chat runs the full Spotify task
flow (status card, cancel, retry, tasks registry); the app runs its
background re-download. `flood` (429) is reported here — the wait
seconds come from the RetryAfter, and each surface shows them in its
own card/JSON.

The track passed in is always a FRESH row read by the caller (plan
§12.4) — never a stale snapshot copy.
"""
from datetime import timedelta
from logging import getLogger

from telegram.constants import ChatAction
from telegram.error import RetryAfter, TelegramError

from utils.library import LibraryTrack

logger = getLogger(__name__)

# Outcomes (see the module docstring for who owns each).
SENT_FILE_ID = "file_id"
SENT_COPIED = "copied"
FLOOD = "flood"
REDOWNLOAD = "redownload"
EXHAUSTED = "exhausted"


def flood_wait_seconds(err: RetryAfter) -> int:
    """Human-sane seconds from a RetryAfter (Bot API 10: integer; PTB
    is deprecating it toward timedelta — accept both)."""
    value = getattr(err, "retry_after", None)
    if isinstance(value, timedelta):
        return max(1, int(value.total_seconds()))
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 5


async def redeliver(bot, *, chat_id: int, track: LibraryTrack, markup=None) -> dict:
    """Run ladder steps 1-2 against one fresh library row.

    Returns `{"state": ..., "retry_after": int | None}`:
      - `file_id` / `copied`  — delivered; the caller counts the request
        (library.record_request) and shows its success state
      - `flood`               — 429; `retry_after` carries the seconds
      - `redownload`          — no usable cloud handles, but a Spotify
        URL is stored: the caller must re-download from it
      - `exhausted`           — nothing left to try: the caller shows
        its own honest failure (never a silent skip)
    """
    if track.file_id:
        try:
            await bot.send_chat_action(
                chat_id=chat_id, action=ChatAction.UPLOAD_VOICE
            )
        except TelegramError:
            pass  # the chat action is cosmetic; the send decides
        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=track.file_id,
                title=track.title or None,
                performer=track.artist or None,
                duration=track.duration_sec or None,
                reply_markup=markup,
                protect_content=False,
            )
        except RetryAfter as err:
            return {"state": FLOOD, "retry_after": flood_wait_seconds(err)}
        except TelegramError:
            logger.warning(
                "library re-send by file_id failed for %r; "
                "trying copyMessage", track.track_key, exc_info=True,
            )
        else:
            return {"state": SENT_FILE_ID}

    if track.orig_chat_id is not None and track.orig_message_id is not None:
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=track.orig_chat_id,
                message_id=track.orig_message_id,
                reply_markup=markup,
                protect_content=False,
            )
        except RetryAfter as err:
            return {"state": FLOOD, "retry_after": flood_wait_seconds(err)}
        except TelegramError:
            # the original message is gone (deleted chat, purged) — the
            # re-download is the only honest left
            logger.warning(
                "library copyMessage failed for %r; "
                "falling back to re-download", track.track_key, exc_info=True,
            )
        else:
            return {"state": SENT_COPIED}

    if track.spotify_url:
        return {"state": REDOWNLOAD}
    return {"state": EXHAUSTED}
