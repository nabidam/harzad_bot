"""Spotify text search & interactive track cards (plan 1.2).

Plain text in the music states is treated as a query: the top matches
from the same Spotify backend spotdl uses at boot are rendered as photo
cards with [⬇️ Download MP3] / [🎧 30s Preview] / [📄 Lyrics] /
[🔗 Open on Spotify] buttons. The rendered candidates are stashed in
user_data["song_candidates"] so the callback buttons can resolve them —
callback_data can only carry the card index. Previews come from the
iTunes/Deezer fallback (plan 1.4); lyrics land in Phase 2.4.
"""
import asyncio
import re
import uuid
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError

from connectors.preview import resolve_preview_url
from connectors.spotify_search import TrackResult, format_duration, search_tracks
from utils import keyboards, progress
from utils.constants.keyboards import (
    SONG_ACTION_CALLBACK_PATTERN,
    SEARCH_CANCEL_CALLBACK_PATTERN,
)
from utils.constants.messages import (
    COMMING_SOON,
    NO_PREVIEW,
    NO_RESULTS_FOUND,
    SEARCH_CANCELLED,
    SEARCH_FAILED,
    SEARCHING,
    SEARCHING_TICK,
    SONG_EXPIRED,
    STALE_SEARCH_CANCEL,
    TRACK_CARD,
)
from utils.constants.states import MUSIC_STATE
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html

from bot.handlers.music.spotify import download as spotify_download

# Init logger
logger = getLogger(__name__)

# user_data key holding the rendered candidates (per user, in memory —
# a process restart or a newer search invalidates old card buttons).
CANDIDATES_KEY = "song_candidates"
# user_data key holding the live search's (seq, asyncio.Event) cancel
# token, set by run_search and consumed by on_search_cancel.
CANCEL_KEY = "search_cancel"


def track_caption(track: TrackResult) -> str:
    """HTML caption for one result card; track fields are escaped."""
    album_line = f"\n📀 {escape_html(track.album)}" if track.album else ""
    return TRACK_CARD.format(
        title=escape_html(track.title),
        artist=escape_html(track.artist),
        album_line=album_line,
        duration=format_duration(track.duration_sec),
    )


async def _send_card(update: Update, context: MyContext, index: int, track: TrackResult) -> None:
    """One result as a photo card, falling back to a plain-text card when
    the cover is missing or Telegram cannot fetch it."""
    markup = keyboards.track_keyboard(index, track.spotify_url)
    caption = track_caption(track)
    chat_id = update.effective_chat.id
    if track.cover_url:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=track.cover_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                protect_content=True,
            )
            return
        except TelegramError:
            logger.warning(
                "Cover %s not fetchable; falling back to text card",
                track.cover_url, exc_info=True,
            )
    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def run_search(update: Update, context: MyContext, query: str) -> None:
    """Search Spotify for a plain-text query and send up to 3 track cards.

    Core (undecorated) so both music states reuse it from their own TEXT
    handlers without a second DB row or chat action per message.

    While the (slow) search runs, a live status message keeps the user
    informed: an instant "Searching for …" with a Cancel button, a
    re-sent typing indicator and an elapsed counter every few seconds.
    The status message is ephemeral — the final answer (cards, no
    results, failure, cancelled) is a clean message without a dead
    Cancel button.
    """
    assert update.effective_chat is not None
    assert update.effective_message is not None
    query = (query or "").strip()
    if not query:
        return

    message = update.effective_message
    chat_id = update.effective_chat.id
    safe_query = escape_html(query)

    # Fresh cancel token per search (see on_search_cancel). A second
    # search typed while the first is still running orphans the first
    # token: the first status finishes on its own, this one wins.
    seq = uuid.uuid4().hex[:8]
    cancel_event = asyncio.Event()
    context.user_data[CANCEL_KEY] = (seq, cancel_event)

    async with progress.live_status(
        context.bot,
        chat_id,
        SEARCHING.format(query=safe_query),
        reply_markup=keyboards.search_cancel_keyboard(seq),
        parse_mode=ParseMode.HTML,
        tick_text=lambda elapsed: SEARCHING_TICK.format(query=safe_query, elapsed=elapsed),
    ) as status:
        cancelled, results, failed = await _await_search_or_cancel(query, cancel_event)

    # Ephemeral status: remove it so no dead Cancel button lingers
    # (best-effort — the user may have deleted it already).
    try:
        await status.delete()
    except TelegramError:
        logger.debug("search status message already gone", exc_info=True)

    if cancelled:
        await message.reply_text(SEARCH_CANCELLED, parse_mode=ParseMode.HTML)
        return
    if failed:
        await message.reply_text(SEARCH_FAILED, parse_mode=ParseMode.HTML)
        return
    if not results:
        await message.reply_text(
            NO_RESULTS_FOUND.format(query=safe_query),
            parse_mode=ParseMode.HTML,
        )
        return

    context.user_data[CANDIDATES_KEY] = results
    for index, track in enumerate(results):
        await _send_card(update, context, index, track)


async def _await_search_or_cancel(query: str, cancel_event: asyncio.Event):
    """Race the Spotify search against the user's Cancel button.

    Returns `(cancelled, results, failed)`. The search runs as a task;
    cancelling stops the wait — the worker thread itself cannot be
    killed, its result is simply discarded. If both land on the same
    tick, the finished search wins (the work is done; don't throw it
    away).
    """
    search_task = asyncio.create_task(search_tracks(query))
    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        done, _pending = await asyncio.wait(
            {search_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        # the whole handler was torn down (shutdown) — take both tasks
        # with it
        search_task.cancel()
        cancel_task.cancel()
        await asyncio.gather(search_task, cancel_task, return_exceptions=True)
        raise

    cancelled = cancel_task in done and search_task not in done
    for task in (search_task, cancel_task):
        if not task.done():
            task.cancel()
    await asyncio.gather(search_task, cancel_task, return_exceptions=True)

    if cancelled:
        return True, None, False
    try:
        return False, search_task.result(), False
    except Exception:
        # backend/network hiccup — tell the user instead of dropping
        # the update into the generic error handler
        logger.warning("Spotify search failed for %r", query, exc_info=True)
        return False, None, True


async def on_search_cancel(update: Update, context: MyContext):
    """Cancel button on the live search status message (2026-10-27 UX).

    Sets the event that run_search is racing against; run_search then
    turns its status message into the "cancelled" answer. Stale buttons
    (a newer search replaced the token, or the process restarted) just
    get a toast — they must not cancel someone else's in-flight search.
    """
    cq = update.callback_query
    assert cq is not None
    match = re.match(SEARCH_CANCEL_CALLBACK_PATTERN, cq.data or "")
    assert match is not None  # the handler's pattern guarantees this
    seq = match.group(1)

    entry = context.user_data.get(CANCEL_KEY)
    if not entry or entry[0] != seq:
        await cq.answer(STALE_SEARCH_CANCEL)
        return
    entry[1].set()
    await cq.answer()


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: MyContext):
    """TEXT entry for MUSIC_STATE: any plain text is a song query."""
    assert update.message is not None
    await run_search(update, context, update.message.text or "")
    return MUSIC_STATE


async def on_song_action(update: Update, context: MyContext):
    """Buttons on the search-result cards (plan 1.2).

    `song:dl:<i>` downloads the i-th candidate via the shared spotdl
    core; `song:lyr:<i>` is the Phase 2.4 lyrics slot (coming-soon for
    now). Registered in every conversation state and in group 1, before
    the catch-all callback handlers.
    """
    cq = update.callback_query
    assert cq is not None
    match = re.match(SONG_ACTION_CALLBACK_PATTERN, cq.data or "")
    assert match is not None  # the handler's pattern guarantees this
    action, index = match.group(1), int(match.group(2))

    candidates = context.user_data.get(CANDIDATES_KEY) or []
    if index >= len(candidates):
        # stale card: a newer search replaced the candidates, or the
        # process restarted (user_data is in-memory)
        await cq.answer(SONG_EXPIRED, show_alert=True)
        return

    track = candidates[index]
    if action == "lyr":
        await cq.answer(COMMING_SOON, show_alert=True)
        return

    if action == "prev":
        await _send_preview(cq, update, context, track)
        return

    await cq.answer()
    await spotify_download.run_spotify_download(update, context, track.spotify_url)


async def _send_preview(
    cq, update: Update, context: MyContext, track: TrackResult
) -> None:
    """Send the 30s preview clip for a card track (plan 1.4).

    Spotify's own preview_url when the item carries one; otherwise the
    iTunes/Deezer fallback (the free backend never does). Telegram fetches
    the MP3 URL itself — no staging on disk.
    """
    assert update.effective_chat is not None
    url = track.preview_url
    if not url:
        url = await resolve_preview_url(track.title, track.artist)
    if not url:
        await cq.answer(NO_PREVIEW, show_alert=True)
        return
    await cq.answer()
    await context.bot.send_audio(
        chat_id=update.effective_chat.id,
        audio=url,
        title=track.title,
        performer=track.artist,
        protect_content=True,
    )
