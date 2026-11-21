"""Per-user music library in chat (docs/music-library-plan.md).

`📚 My music` opens ONE editable card — the same tokenized carousel
pattern as the search result card (spec §5.3): the token names a frozen
page snapshot (300 s TTL in bot_data), every button payload carries the
exact track position, a stale token gets a toast, and an owner check
plus a private-chat gate keep a shared or foreign card inert (plan §3
group safety: nothing library-related is ever rendered into a group).

"Get again" re-delivers from Telegram's cloud storage (plan §3 ladder):
sendAudio by the stored file_id (server-side relay — no re-upload, no
disk), then copyMessage of the original delivery, then a full
re-download of the Spotify URL (the existing task flow, which refreshes
the row's cloud handles on success). An exhausted ladder gets an
honest failure answer — never a silent skip.
"""
import math
import re
from datetime import timedelta
from logging import getLogger

from telegram import InputMediaPhoto, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import RetryAfter, TelegramError

from connectors.preview import resolve_preview_url
from utils import copy, keyboards, library
from utils.ack import answer_callback
from utils.constants.keyboards import (
    LIB_ACTION_CALLBACK_PATTERN,
    LIB_PAGE_CALLBACK_PATTERN,
)
from utils.custom_context import MyContext
from utils.library import LibraryTrack
from utils.tokens import get_token_store

from bot.handlers.music.spotify import download as spotify_download

# Init logger
logger = getLogger(__name__)

# bot_data store name for frozen page snapshots:
# token -> {"tg_id", "page", "pages", "total", "tracks"}.
LIB_STORE = "library_pages"
# Same staleness window as the search result sets (spec §5.3).
LIB_TTL_SECONDS = 300


def _message_is_card(message) -> bool:
    """True when the clicked message can be edited into another card.

    A photo/text card yes; a delivered audio (the re-send markup lives
    on it too) never — editing would clobber the user's file.
    """
    if message is None:
        return False
    for media in ("audio", "video", "document", "animation", "voice",
                  "video_note"):
        if getattr(message, media, None):
            return False
    return True


def _flood_wait_seconds(err) -> int:
    """Human-sane seconds from a RetryAfter (Bot API 10: integer; PTB
    is deprecating it toward timedelta — accept both)."""
    value = getattr(err, "retry_after", None)
    if isinstance(value, timedelta):
        return max(1, int(value.total_seconds()))
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 5


async def _send_card(context, chat_id: int, track: LibraryTrack,
                     caption: str, markup) -> None:
    """Send the one library card, photo or plain text (cover fetch is
    Telegram-side; an unfetchable cover degrades to the text card —
    same property as the search card)."""
    if track.cover_url:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=track.cover_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                protect_content=False,
            )
            return
        except TelegramError:
            logger.warning(
                "Library cover %s not fetchable; falling back to text card",
                track.cover_url, exc_info=True,
            )
    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def _edit_card(cq, context: MyContext, token: str, entry: dict,
                     index: int) -> None:
    """Edit the existing card to `index` of `entry` (carousel / page
    change). A non-card message (e.g. a re-sent audio) is never edited
    in place — the card goes out as a fresh message instead."""
    message = cq.message
    if message is None:
        logger.warning("library card has no message to edit; skipping")
        return
    track = entry["tracks"][index - 1]
    caption = library.library_caption(track, index, len(entry["tracks"]))
    markup = keyboards.library_keyboard(
        token, index=index, total=len(entry["tracks"]),
        page=entry["page"], pages=entry["pages"],
    )
    if not _message_is_card(message):
        await _send_card(context, message.chat_id, track, caption, markup)
        return
    if track.cover_url:
        try:
            await context.bot.edit_message_media(
                chat_id=message.chat_id,
                media=InputMediaPhoto(
                    media=track.cover_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                ),
                message_id=message.message_id,
                reply_markup=markup,
            )
            return
        except TelegramError:
            logger.warning(
                "Could not edit library card media for cover %s; "
                "editing as text", track.cover_url, exc_info=True,
            )
    try:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            text=caption,
            parse_mode=ParseMode.HTML,
            message_id=message.message_id,
            reply_markup=markup,
        )
    except TelegramError:
        # last resort: a fresh message in the same chat (spec §5.3)
        logger.warning("Library card could not be edited; sending a new card",
                       exc_info=True)
        await _send_card(context, message.chat_id, track, caption, markup)


async def _show_simple(update: Update, context: MyContext, text: str,
                       markup, edit: bool) -> None:
    """The empty / unavailable states (plain text card)."""
    cq = update.callback_query
    if edit and cq is not None and _message_is_card(cq.message):
        try:
            await context.bot.edit_message_text(
                chat_id=cq.message.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                message_id=cq.message.message_id,
                reply_markup=markup,
            )
            return
        except TelegramError:
            logger.warning("library state card could not be edited",
                           exc_info=True)
    chat_id = (
        cq.message.chat_id
        if cq is not None and cq.message is not None
        else update.effective_chat.id
    )
    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def _render(update: Update, context: MyContext, tg_id: int,
                  page: int, *, edit: bool) -> None:
    """Read one page and render the card (fresh message or in-place edit).

    The token names this exact page snapshot for the next 300 s: later
    requests or removes by the owner don't shift what a rendered card
    points at (stale -> toast, never a wrong track).
    """
    data = await library.read_library(tg_id, page)
    if data is None:
        await _show_simple(update, context, copy.LIB_UNAVAILABLE,
                           keyboards.library_unavailable_keyboard(), edit)
        return
    tracks, total = data
    if not tracks:
        await _show_simple(update, context, copy.LIB_NONE,
                           keyboards.library_empty_keyboard(), edit)
        return
    pages = math.ceil(total / library.LIB_PAGE_SIZE)
    token = get_token_store(context, LIB_STORE).put(
        {"tg_id": tg_id, "page": page, "pages": pages, "total": total,
         "tracks": tracks},
        LIB_TTL_SECONDS,
    )
    entry = {"page": page, "pages": pages, "tracks": tracks}
    cq = update.callback_query
    if edit and cq is not None and _message_is_card(cq.message):
        await _edit_card(cq, context, token, entry, 1)
    else:
        track = tracks[0]
        caption = library.library_caption(track, 1, len(tracks))
        markup = keyboards.library_keyboard(
            token, index=1, total=len(tracks), page=page, pages=pages,
        )
        chat_id = (
            cq.message.chat_id
            if cq is not None and cq.message is not None
            else update.effective_chat.id
        )
        await _send_card(context, chat_id, track, caption, markup)


# --- "Get again": the cloud re-send ladder (plan §3) --------------------------

async def _redeliver(cq, update: Update, context: MyContext, token: str,
                     entry: dict, index: int, track: LibraryTrack) -> None:
    """Re-deliver one library track.

    1. sendAudio by file_id — the documented cloud re-send: Telegram
       relays the stored file server-side, no upload from our server,
       no 50 MB limit. The audio keeps its baked-in title/artist/cover;
       no thumbnail parameter (thumbnails can't be re-sent).
    2. copyMessage of the original delivery — only when step 1 failed
       and the original message refs are known.
    3. Full re-download via the Spotify URL (existing task flow) — the
       delivery capture inside it refreshes this row's cloud handles.
    4. Exhausted: an honest "can't re-fetch" answer (spec §7 class 2),
       never a silent skip.
    """
    assert update.effective_chat is not None
    chat_id = update.effective_chat.id
    markup = keyboards.library_keyboard(
        token, index=index, total=len(entry["tracks"]),
        page=entry["page"], pages=entry["pages"],
    )

    if track.file_id:
        try:
            await context.bot.send_chat_action(
                chat_id=chat_id, action=ChatAction.UPLOAD_VOICE
            )
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=track.file_id,
                title=track.title or None,
                performer=track.artist or None,
                duration=track.duration_sec or None,
                reply_markup=markup,
                protect_content=False,
            )
        except RetryAfter as err:
            await cq.message.reply_text(
                copy.LIB_FLOOD_WAIT.format(seconds=_flood_wait_seconds(err)),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.home_tasks_row_keyboard(),
            )
            return
        except TelegramError:
            logger.warning(
                "library re-send by file_id failed for %r; "
                "trying copyMessage", track.track_key, exc_info=True,
            )
        else:
            library.record_request(entry["tg_id"], track.track_key)
            return

    if track.orig_chat_id is not None and track.orig_message_id is not None:
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=track.orig_chat_id,
                message_id=track.orig_message_id,
                reply_markup=markup,
                protect_content=False,
            )
        except RetryAfter as err:
            await cq.message.reply_text(
                copy.LIB_FLOOD_WAIT.format(seconds=_flood_wait_seconds(err)),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards.home_tasks_row_keyboard(),
            )
            return
        except TelegramError:
            # the original message is gone (deleted chat, purged) — the
            # re-download is the only honest left
            logger.warning(
                "library copyMessage failed for %r; "
                "falling back to re-download", track.track_key,
                exc_info=True,
            )
        else:
            library.record_request(entry["tg_id"], track.track_key)
            return

    if track.spotify_url:
        # the existing flow: its own status message, cancel, retry and
        # (on success) the delivery capture that refreshes this row's
        # file_id / message refs for next time
        await spotify_download.run_spotify_download(
            update, context, track.spotify_url
        )
        return

    # ladder exhausted
    await cq.message.reply_text(
        copy.LIB_NO_RESEND,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.home_tasks_row_keyboard(),
    )


async def _send_preview(cq, update: Update, context: MyContext,
                        track: LibraryTrack) -> None:
    """30s preview for a library track: the iTunes/Deezer lookup
    (the free backend's items carry no preview_url), then Telegram
    fetches the MP3 itself — no staging on disk."""
    assert update.effective_chat is not None
    url = await resolve_preview_url(track.title, track.artist)
    if not url:
        await answer_callback(cq, copy.TOAST_TRACK_NO_PREVIEW)
        return
    await answer_callback(cq)
    await context.bot.send_audio(
        chat_id=update.effective_chat.id,
        audio=url,
        title=track.title,
        performer=track.artist or None,
        protect_content=False,
    )


# --- callback handlers (wired in bot/handlers/callbacks.py) -------------------

async def on_library_callback(update: Update, context: MyContext):
    """`📚 My music` (nav:library) — the shared chain, every state.

    Private chats only (plan §3 group safety): the library is the
    tapper's personal list and is never rendered into a group.
    Returns None: opening the library changes no input mode.
    """
    cq = update.callback_query
    assert cq is not None
    chat = update.effective_chat
    if chat is None or chat.type != "private":
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    await answer_callback(cq)
    await _render(update, context, update.effective_user.id, 1, edit=True)


async def on_lib_action(update: Update, context: MyContext):
    """Buttons on the tokenized library card (plan §5).

    `lib:<action>:<token>:<i>` — i is the 1-based position in the
    token's frozen page snapshot. Unknown/expired token, foreign owner,
    or out-of-range index all answer the stale toast and run nothing
    (spec §5.3, §16).
    """
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_ACTION_CALLBACK_PATTERN, cq.data or "")
    assert match is not None  # the handler's pattern guarantees this
    action, token, idx_s = match.group(1), match.group(2), match.group(3)

    entry = get_token_store(context, LIB_STORE).get(token)
    if entry is None or entry["tg_id"] != update.effective_user.id:
        # expired after 300 s, unknown, process restart — or someone
        # else's card (the library is private by construction)
        await answer_callback(cq, copy.TOAST_LIB_STALE)
        return
    index = int(idx_s)
    tracks: list[LibraryTrack] = entry["tracks"]
    if not 1 <= index <= len(tracks):
        # a token can only point at its own snapshot; an out-of-range
        # index is stale by construction
        await answer_callback(cq, copy.TOAST_LIB_STALE)
        return
    track = tracks[index - 1]

    if action in ("prevcard", "next"):
        new_index = index + (1 if action == "next" else -1)
        if new_index < 1:
            await answer_callback(cq, copy.TOAST_LIB_FIRST)
            return
        if new_index > len(tracks):
            await answer_callback(cq, copy.TOAST_LIB_LAST)
            return
        await answer_callback(cq)
        await _edit_card(cq, context, token, entry, new_index)
    elif action == "prev":
        await _send_preview(cq, update, context, track)
    elif action == "dl":
        await answer_callback(cq)
        await _redeliver(cq, update, context, token, entry, index, track)
    elif action == "rm":
        # no early ack: the toast reports the real outcome
        ok = await library.remove_track(entry["tg_id"], track.track_key)
        if not ok:
            await answer_callback(cq, copy.TOAST_LIB_FAILED)
            return
        await answer_callback(cq, copy.TOAST_LIB_REMOVED)
        # fresh page render (the snapshot shrank); a dead page falls
        # back to page 1 inside read_library
        await _render(update, context, entry["tg_id"], entry["page"],
                      edit=True)


async def on_lib_page(update: Update, context: MyContext):
    """`lib:page:<n>` — re-read the DB for page n and re-render.

    A page that no longer exists (rows removed since render) gets the
    stale-page toast; page 0/negative falls back to page 1."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_PAGE_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    tg_id = update.effective_user.id
    page = max(1, int(match.group(1)))

    data = await library.read_library(tg_id, 1)
    if data is None:
        await answer_callback(cq, copy.TOAST_LIB_FAILED)
        return
    total = data[1]
    pages = math.ceil(total / library.LIB_PAGE_SIZE) if total else 1
    if page > pages:
        await answer_callback(cq, copy.TOAST_LIB_PAGE)
        return
    await answer_callback(cq)
    await _render(update, context, tg_id, page, edit=True)
