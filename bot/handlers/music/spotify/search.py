"""Spotify text search & the single editable result card (redesign §5).

Plain text in the Search Music input mode is a song query. The top
matches from the same Spotify backend spotdl uses at boot are shown as
ONE result card — a small in-chat carousel that edits the same message's
photo/caption and keyboard for previous/next matches (spec §5.3). Never
three separate photo messages.

State lives in a shared token map (bot_data, 300 s TTL): the token names
the exact result set, so any user who can see the card can tap it, and a
stale token gets a toast — never "the most recent results for this user"
(spec §5.3, §5.6, §16). The pre-token rollout rendered index-only
payloads; those buttons are unresolvable by design and only ever answer
the stale toast.
"""
import asyncio
import re
import uuid
from logging import getLogger

from telegram import InputMediaPhoto, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError

from connectors.preview import resolve_preview_url
from connectors.spotify_search import TrackResult, format_duration, search_tracks
from utils import copy, keyboards, progress
from utils.constants.keyboards import (
    SONG_ACTION_CALLBACK_PATTERN,
    SONG_ACTION_LEGACY_PATTERN,
    SEARCH_AGAIN_CALLBACK_PATTERN,
    SEARCH_CANCEL_CALLBACK_PATTERN,
)
from utils.constants.states import SEARCH_INPUT_STATE
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html
from utils.tokens import get_token_store

from bot.handlers.music.spotify import download as spotify_download

# Init logger
logger = getLogger(__name__)

# user_data key holding the live search's (seq, asyncio.Event) cancel
# token, set by run_search and consumed by on_search_cancel. Per user:
# the Cancel button lives on the searcher's own status message.
CANCEL_KEY = "search_cancel"

# bot_data store name for result sets: token -> {"query", "tracks"}.
# Shared across users on purpose (spec §5.6): a card delivered into a
# group resolves for whoever taps it.
RESULTS_STORE = "search_results"
# Spec §5.3: cache the result set for 300 seconds.
RESULTS_TTL_SECONDS = 300

# Spec §4.4 proposed guard for Spotify text search (owner decision: the
# recommended default applies; production telemetry should validate it
# before it is treated as a measured fact). The backend's own free-API
# search has been observed around 45 s, so 90 s means "something is
# wrong", not "slow but normal".
SEARCH_TIMEOUT_SECONDS = 90


def card_caption(track: TrackResult, index: int, total: int) -> str:
    """HTML caption for the result card (copy.track_caption, spec §5.3).
    Track fields are escaped; missing album/duration omit their
    component, never render None."""
    return copy.track_caption(
        title=escape_html(track.title),
        artist=escape_html(track.artist),
        album=escape_html(track.album) if track.album else None,
        duration=format_duration(track.duration_sec),
        index=index,
        total=total,
    )


def card_markup(token: str, index: int, track: TrackResult):
    """The card's keyboard for one match (spec §5.3 row layout)."""
    return keyboards.track_keyboard(
        token, index=index, spotify_url=track.spotify_url or None
    )


async def _send_card(
    update: Update, context: MyContext, token: str, index: int, total: int
) -> None:
    """Send the one result card (first match), photo or plain text."""
    assert update.effective_chat is not None
    track = get_token_store(context, RESULTS_STORE).get(token)["tracks"][index - 1]
    chat_id = update.effective_chat.id
    caption = card_caption(track, index, total)
    markup = card_markup(token, index, track)
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


async def _edit_card(
    cq, context: MyContext, token: str, index: int, total: int
) -> None:
    """Edit the existing card message to another match (carousel).

    Telegram can always replace a text message; replacing media fails on
    some message states, in which case the same message is edited to the
    text card. A brand-new message is the last resort (spec §5.3).
    """
    store = get_token_store(context, RESULTS_STORE)
    track = store.get(token)["tracks"][index - 1]
    caption = card_caption(track, index, total)
    markup = card_markup(token, index, track)
    message = cq.message
    chat_id = cq.message.chat_id
    message_id = cq.message.message_id
    if track.cover_url:
        try:
            await context.bot.edit_message_media(
                chat_id=chat_id,
                media=InputMediaPhoto(
                    media=track.cover_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                ),
                message_id=message_id,
                reply_markup=markup,
            )
            return
        except TelegramError:
            logger.warning(
                "Could not edit card media for cover %s; editing as text",
                track.cover_url, exc_info=True,
            )
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            text=caption,
            parse_mode=ParseMode.HTML,
            message_id=message_id,
            reply_markup=markup,
        )
    except TelegramError:
        # last resort: a fresh message in the same chat (spec §5.3)
        logger.warning("Card message could not be edited; sending a new card",
                       exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def run_search(update: Update, context: MyContext, query: str) -> None:
    """Search Spotify for a plain-text query and show the result card.

    Core (undecorated) so the Search Music TEXT handler reuses it without
    a second DB row or chat action per message.

    While the (slow) search runs, the status engine (utils/progress)
    keeps the user informed: an instant accepted message with Cancel +
    Tasks buttons, then measured-elapsed phases at 5/30/60 s (spec
    §4.2) — no fake progress, no invented ETA. The status message is
    ephemeral — the final answer (the result card, no results, failure,
    cancelled, timed out) is a clean message without a dead Cancel
    button.

    Spec §4.4: a search that has not answered by SEARCH_TIMEOUT_SECONDS
    ends in the timeout card (Search again + Home). The backend's
    worker thread cannot be killed, so the timeout detaches the wait
    and discards any late result — the copy never claims the backend
    stopped.
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
        copy.SEARCH_WORKING.format(query=safe_query),
        reply_markup=keyboards.search_cancel_keyboard(seq),
        parse_mode=ParseMode.HTML,
        stages=[
            (progress.STAGE_RESOLVING,
             lambda e: copy.SEARCH_WORKING_TICK.format(query=safe_query, elapsed=e)),
            (progress.STAGE_SLOW,
             lambda e: copy.SEARCH_SLOW.format(query=safe_query, elapsed=e)),
            (progress.STAGE_STILL_WORKING,
             lambda e: copy.SEARCH_VERY_SLOW.format(query=safe_query, elapsed=e)),
        ],
    ) as status:
        cancelled, timed_out, results, failed = await _await_search_or_cancel(
            query, cancel_event
        )

    # Ephemeral status: remove it so no dead Cancel button lingers
    # (best-effort — the user may have deleted it already).
    try:
        await status.delete()
    except TelegramError:
        logger.debug("search status message already gone", exc_info=True)

    if cancelled:
        await message.reply_text(copy.SEARCH_CANCELLED, parse_mode=ParseMode.HTML)
        return
    if timed_out:
        # The timed-out query is stored (empty result set) so [Search
        # again] reuses the search:again path and its 300 s stale rule
        # (spec §5.4). The backend may still finish in the background —
        # its late result is discarded, never sent after the timeout.
        retry_token = get_token_store(context, RESULTS_STORE).put(
            {"query": query, "tracks": []}, RESULTS_TTL_SECONDS
        )
        await message.reply_text(
            copy.SEARCH_TIMEOUT,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.search_timeout_keyboard(retry_token),
        )
        return
    if failed:
        await message.reply_text(copy.SEARCH_FAILED, parse_mode=ParseMode.HTML)
        return
    if not results:
        await message.reply_text(
            copy.SEARCH_NO_RESULTS.format(query=safe_query),
            parse_mode=ParseMode.HTML,
        )
        return

    # The token names this exact result set for the next 300 s (spec
    # §5.3). Shared store: any user who can see the card can tap it.
    token = get_token_store(context, RESULTS_STORE).put(
        {"query": query, "tracks": results}, RESULTS_TTL_SECONDS
    )
    await _send_card(update, context, token, 1, len(results))


async def _await_search_or_cancel(
    query: str, cancel_event: asyncio.Event, timeout_seconds: float | None = None
):
    """Race the Spotify search against the user's Cancel button and the
    spec §4.4 timeout guard.

    Returns `(cancelled, timed_out, results, failed)`. The search runs
    as a task; cancelling or timing out stops the WAIT — the worker
    thread itself cannot be killed, its late result is simply
    discarded (the copy must never claim the backend stopped). If the
    search lands on the same tick as the other two, the finished
    search wins (the work is done; don't throw it away). An explicit
    Cancel beat by the user takes precedence over the guard.
    """
    if timeout_seconds is None:
        timeout_seconds = SEARCH_TIMEOUT_SECONDS
    search_task = asyncio.create_task(search_tracks(query))
    cancel_task = asyncio.create_task(cancel_event.wait())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
    try:
        done, _pending = await asyncio.wait(
            {search_task, cancel_task, timeout_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        # the whole handler was torn down (shutdown) — take all three
        # tasks with it
        for task in (search_task, cancel_task, timeout_task):
            task.cancel()
        await asyncio.gather(
            search_task, cancel_task, timeout_task, return_exceptions=True
        )
        raise

    search_done = search_task.done()
    cancelled = not search_done and cancel_task in done
    timed_out = not search_done and not cancelled and timeout_task in done
    for task in (search_task, cancel_task, timeout_task):
        if not task.done():
            task.cancel()
    await asyncio.gather(
        search_task, cancel_task, timeout_task, return_exceptions=True
    )

    if cancelled:
        return True, False, None, False
    if timed_out:
        logger.warning(
            "Spotify search for %r timed out after %.0fs", query, timeout_seconds
        )
        return False, True, None, False
    try:
        return False, False, search_task.result(), False
    except Exception:
        # backend/network hiccup — tell the user instead of dropping
        # the update into the generic error handler
        logger.warning("Spotify search failed for %r", query, exc_info=True)
        return False, False, None, True


async def on_search_cancel(update: Update, context: MyContext):
    """Cancel button on the live search status message.

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
        await cq.answer(copy.TOAST_SEARCH_CANCEL_STALE)
        return
    entry[1].set()
    await cq.answer()


async def on_song_action(update: Update, context: MyContext):
    """Buttons on the tokenized result card (spec §5.3, §5.6).

    `song:dl:<token>:<i>` / `song:prev:<token>:<i>` — the in-chat
    carousel card (i is the 1-based match position); without the index
    the token names a single shared track (inline cards, any user may
    tap). `song:prevcard`/`song:next` edit the card to the adjacent
    match. An unknown or expired token answers the stale toast and runs
    nothing (spec §5.3, §16).
    """
    cq = update.callback_query
    assert cq is not None
    match = re.match(SONG_ACTION_CALLBACK_PATTERN, cq.data or "")
    assert match is not None  # the handler's pattern guarantees this
    action, token, idx_s = match.group(1), match.group(2), match.group(3)

    entry = get_token_store(context, RESULTS_STORE).get(token)
    if entry is None:
        # expired after 300 s, unknown, or the process restarted
        # (the store is in-memory, spec §2.5)
        await cq.answer(copy.TOAST_SEARCH_STALE)
        return
    tracks = entry["tracks"]

    if idx_s is None:
        # shared inline card: the card IS the track (spec §5.6)
        track = tracks[0]
    else:
        index = int(idx_s)
        if not 1 <= index <= len(tracks):
            # a token can only point at its own result set; an out-of-
            # range index is stale by construction
            await cq.answer(copy.TOAST_SEARCH_STALE)
            return
        track = tracks[index - 1]

    if action == "dl":
        await cq.answer()
        await spotify_download.run_spotify_download(
            update, context, track.spotify_url
        )
    elif action == "prev":
        await _send_preview(cq, update, context, track)
    elif action in ("prevcard", "next"):
        # only rendered on indexed (in-chat) cards
        new_index = index + (1 if action == "next" else -1)
        if new_index < 1:
            await cq.answer(copy.TOAST_TRACK_FIRST)
            return
        if new_index > len(tracks):
            await cq.answer(copy.TOAST_TRACK_LAST)
            return
        await _edit_card(cq, context, token, new_index, len(tracks))


async def on_song_action_legacy(update: Update, context: MyContext):
    """Pre-token rollout card buttons (index-only payloads).

    The index alone cannot be resolved to the result set it belonged to
    without guessing "the user's latest search" — the most-recent
    fallback the redesign forbids (spec §16). Toast only.
    """
    cq = update.callback_query
    assert cq is not None
    await cq.answer(copy.TOAST_SEARCH_STALE)


async def on_search_again(update: Update, context: MyContext):
    """[🔎 Search again] on the result card: re-run the same query with
    a fresh result set (spec §5.3)."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(SEARCH_AGAIN_CALLBACK_PATTERN, cq.data or "")
    assert match is not None  # the handler's pattern guarantees this
    entry = get_token_store(context, RESULTS_STORE).get(match.group(1))
    if entry is None:
        await cq.answer(copy.TOAST_SEARCH_STALE)
        return
    await cq.answer()
    await run_search(update, context, entry["query"])


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: MyContext):
    """TEXT entry for the Search Music input mode (spec §2.2, §6.1):
    any plain text is a song query."""
    assert update.message is not None
    await run_search(update, context, update.message.text or "")
    return SEARCH_INPUT_STATE


async def _send_preview(
    cq, update: Update, context: MyContext, track: TrackResult
) -> None:
    """Send the 30s preview clip for a card track.

    Spotify's own preview_url when the item carries one; otherwise the
    iTunes/Deezer fallback (the free backend never does). Telegram fetches
    the MP3 URL itself — no staging on disk.
    """
    assert update.effective_chat is not None
    url = track.preview_url
    if not url:
        url = await resolve_preview_url(track.title, track.artist)
    if not url:
        await cq.answer(copy.TOAST_TRACK_NO_PREVIEW)
        return
    await cq.answer()
    await context.bot.send_audio(
        chat_id=update.effective_chat.id,
        audio=url,
        title=track.title,
        performer=track.artist,
        protect_content=True,
    )
