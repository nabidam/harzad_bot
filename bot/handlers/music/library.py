"""Per-user music library in chat, UX v3 (plan §12, expert design
`docs/music-library-ux-design.md`).

`📚 My music` opens a destination menu (M01): 🕒 Latest, 🔎 Search,
⭐ Often played, 📄 Browse all (Phase B adds 🎼 Playlists above the
escape row — no dead buttons before a handler exists, spec §10). All
track lists are one shared 5-track card (M02) with numbered
`[▶ n] [⋯ n]` rows — one direct re-send and one secondary action per
track, never three competing buttons. `⋯ n` opens the track detail
(M05), the management screen: Get again, 30s Preview, Open on Spotify
(URL button), Remove with confirmation (M05b), Back to tracks.

Library search (M03) is a conversation state: the bot asks for a name,
the next text message is a query over the user's own library (never a
Spotify search — those flows stay separate), and the results reuse the
shared list card (M06) with `[🔎 New search]`. `/cancel` and the
✖ Cancel button stop it. A pasted media link during the prompt hands
off to the normal download flow (owner decision, plan §12.1.1).

All cards are plain text (no cover images — owner decision), rendered
by in-place edit of the clicked message with a fresh-message fallback.
Every item-level button carries a tokenized snapshot (300 s TTL in
bot_data, plan §12.4): the token names a frozen page or detail, a
stale/foreign/out-of-range button gets a toast and runs nothing, and
an owner check plus a private-chat gate keep a shared or foreign card
inert (plan §3 group safety: nothing library-related is ever rendered
into a group).

"Get again" re-delivers via the plan §3 ladder against a FRESH row
read (a parallel delivery may have just captured cloud handles):
sendAudio by file_id, copyMessage of the original delivery, full
re-download of the Spotify URL. An exhausted ladder gets an honest
failure card — never a silent skip.
"""
import math
import re
from datetime import timedelta
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import RetryAfter, TelegramError

from connectors.preview import resolve_preview_url
from utils import copy, keyboards, library
from utils.ack import answer_callback
from utils.constants.keyboards import (
    LIB_DETAIL_CALLBACK_PATTERN,
    LIB_PAGE_CALLBACK_PATTERN,
    LIB_ROW_CALLBACK_PATTERN,
    LIB_SEARCH_PAGE_CALLBACK_PATTERN,
    LIB_SORT_CALLBACK_PATTERN,
)
from utils.constants.states import (
    HOME_STATE,
    LIBRARY_SEARCH_STATE,
)
from utils.custom_context import MyContext
from utils.helpers import escape_html
from utils.library import LibraryTrack
from utils.tokens import get_token_store

from bot.handlers.music.spotify import download as spotify_download

# Init logger
logger = getLogger(__name__)

# bot_data store name for frozen list/detail snapshots (plan §12.4):
#   list:   {source, sort, query, page, pages, total, tracks}
#           — tracks is the rendered page (browse-family) or the FULL
#           result set (search; its pages slice from the freeze)
#   detail: {source: "detail", track_key, track, origin}
#           — origin is the list context the detail was opened from
#   (+ tg_id on both — the owner check)
LIB_STORE = "library_pages"
# Same staleness window as the search result sets (spec §5.3).
LIB_TTL_SECONDS = 300
# user_data slot for the search-prompt card (so /cancel can edit it).
LIB_SEARCH_CARD_KEY = "lib_search_card"

# Browse-all sort -> M02 heading label (plan §12.2 M02).
_SORT_LABELS = {
    "recent": copy.LIB_SOURCE_RECENT,
    "added": copy.LIB_SOURCE_ADDED,
    "az": copy.LIB_SOURCE_AZ,
}
# lib:page:<src> payload -> (library source, sort).
_PAGE_SOURCES = {
    "latest": ("latest", None),
    "often": ("often", None),
    "recent": ("browse", "recent"),
    "added": ("browse", "added"),
    "az": ("browse", "az"),
}


# --- shared guards / rendering primitives --------------------------------------

def _private_ok(update: Update) -> bool:
    """The library is private by construction (plan §3): group taps
    get one toast, nothing renders, no library data is exposed."""
    chat = update.effective_chat
    return chat is not None and chat.type == "private"


def _message_is_card(message) -> bool:
    """True when the clicked message can be edited into another card.

    A text card yes; a delivered audio (the re-send markup lives on it
    too) never — editing would clobber the user's file.
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


async def _show_text(update: Update, context: MyContext, text: str,
                     markup, *, edit: bool,
                     edit_message_id: int | None = None) -> int | None:
    """Render one text card: in-place edit of the clicked (or named)
    message when it allows it, otherwise a fresh message. A failed
    edit degrades to a fresh message (spec §5.3). Returns the message
    id when known (the search-prompt card stores it for /cancel)."""
    cq = update.callback_query
    target_id = edit_message_id
    if target_id is None and edit and cq is not None:
        target_id = (cq.message.message_id
                     if _message_is_card(cq.message) else None)
    if target_id is not None:
        chat_id = (cq.message.chat_id if cq is not None
                   else update.effective_chat.id)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                message_id=target_id,
                reply_markup=markup,
            )
            return target_id
        except TelegramError:
            logger.warning("library card could not be edited; "
                           "sending a new card", exc_info=True)
    chat_id = (
        cq.message.chat_id
        if cq is not None and cq.message is not None
        else update.effective_chat.id
    )
    message = await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
    return getattr(message, "message_id", None)


# --- snapshots (plan §12.4) -----------------------------------------------------

def _put_list_snapshot(context: MyContext, tg_id: int, source: str,
                       sort: str | None, query: str | None, page: int,
                       pages: int, total: int,
                       tracks: list[LibraryTrack]) -> str:
    """Register one frozen list render; the token names exactly this
    page for 300 s — later requests or removes don't shift what a
    rendered card points at (stale -> toast, never a wrong track).
    `tracks` is the rendered page (browse-family) or the FULL result
    set (search — its pages slice from the freeze)."""
    return get_token_store(context, LIB_STORE).put(
        {"source": source, "tg_id": tg_id, "sort": sort, "query": query,
         "page": page, "pages": pages, "total": total, "tracks": tracks},
        LIB_TTL_SECONDS,
    )


def _put_detail_snapshot(context: MyContext, tg_id: int, track: LibraryTrack,
                         origin: dict) -> str:
    """Register one frozen detail (M05). `origin` is the list context
    the detail was opened from — Back/remove restore THAT list
    (re-read fresh from the DB), design §5.1."""
    return get_token_store(context, LIB_STORE).put(
        {"source": "detail", "tg_id": tg_id, "track_key": track.track_key,
         "track": track, "origin": origin},
        LIB_TTL_SECONDS,
    )


def _page_rows(entry: dict) -> list[LibraryTrack]:
    """The rows of the entry's current page (search: slice of the
    frozen result set; browse-family: the frozen page itself)."""
    tracks: list[LibraryTrack] = entry["tracks"]
    if entry["source"] != "search":
        return tracks
    page_size = library.LIB_PAGE_SIZE
    start = (entry["page"] - 1) * page_size
    return tracks[start:start + page_size]


def _origin_from_list(entry: dict) -> dict:
    return {"source": entry["source"], "sort": entry.get("sort"),
            "query": entry.get("query"), "page": entry["page"]}


def _list_markup(token: str, entry: dict):
    """The source list's keyboard for a snapshot — attached to re-sent
    audio so the actions keep working from the delivered message too
    (plan §12.1.4)."""
    if entry["source"] == "search":
        return keyboards.library_search_results_keyboard(
            token, page=entry["page"], pages=entry["pages"],
            count=len(_page_rows(entry)),
            start=(entry["page"] - 1) * library.LIB_PAGE_SIZE + 1,
        )
    return keyboards.library_list_keyboard(
        token, source=entry["source"], sort=entry.get("sort"),
        page=entry["page"], pages=entry["pages"],
        count=len(_page_rows(entry)),
    )


# --- card rendering (plan §12.2) -------------------------------------------------

async def _unavailable_card(update: Update, context: MyContext, *,
                            edit: bool,
                            edit_message_id: int | None = None) -> None:
    """S1 — DB down (never looks like the empty library, design
    §10.5)."""
    await _show_text(update, context, copy.LIB_UNAVAILABLE,
                     keyboards.library_unavailable_keyboard(),
                     edit=edit, edit_message_id=edit_message_id)


def _menu_text(total: int) -> str:
    return "\n\n".join([
        copy.LIB_MENU_TITLE,
        copy.LIB_MENU_COUNT.format(count=total,
                                   word=library._tracks_word(total)),
        copy.LIB_MENU_DESC,
        copy.LIB_MENU_PROMPT,
    ])


async def _render_menu(update: Update, context: MyContext, tg_id: int, *,
                       edit: bool, edit_message_id: int | None = None):
    """M01 — the destination menu (or S0 empty / S1 unavailable)."""
    data = await library.library_page(tg_id, "latest", page=1)
    if data is None:
        await _unavailable_card(update, context, edit=edit,
                                edit_message_id=edit_message_id)
        return
    total = data[1]
    if total == 0:
        await _show_text(update, context, copy.LIB_NONE,
                         keyboards.library_empty_keyboard(),
                         edit=edit, edit_message_id=edit_message_id)
        return
    await _show_text(update, context, _menu_text(total),
                     keyboards.library_menu_keyboard(),
                     edit=edit, edit_message_id=edit_message_id)


def _list_head(source: str, sort: str | None) -> str:
    if source == "browse":
        return _SORT_LABELS.get(sort or "recent", copy.LIB_SOURCE_RECENT)
    if source == "often":
        return copy.LIB_SOURCE_OFTEN
    return copy.LIB_SOURCE_LATEST


async def _render_list(update: Update, context: MyContext, tg_id: int,
                       source: str, sort: str | None = None,
                       query: str | None = None, page: int = 1, *,
                       edit: bool, edit_message_id: int | None = None,
                       frozen_tracks: list[LibraryTrack] | None = None):
    """M02/M06 — one track list page.

    Search renders from the frozen FULL result set (`frozen_tracks`,
    or a fresh `search_library` read when not given) — result
    positions never shift under a rendered card (design §6.6).
    Browse-family sources re-read the DB fresh; every render
    registers a new snapshot (plan §12.4).
    """
    if source == "search":
        if frozen_tracks is None:
            frozen_tracks = await library.search_library(tg_id, query or "")
            if frozen_tracks is None:
                await _unavailable_card(update, context, edit=edit,
                                        edit_message_id=edit_message_id)
                return
        total = len(frozen_tracks)
        if total == 0:
            # zero results (also a Back to a now-empty result set)
            await _show_text(
                update, context,
                library.library_search_none_caption(query or ""),
                keyboards.library_search_none_keyboard(),
                edit=edit, edit_message_id=edit_message_id,
            )
            return
        pages = math.ceil(total / library.LIB_PAGE_SIZE)
        page = min(page, pages)
        start = (page - 1) * library.LIB_PAGE_SIZE
        page_tracks = frozen_tracks[start:start + library.LIB_PAGE_SIZE]
    else:
        data = await library.library_page(
            tg_id, source, sort=sort, page=page)
        if data is None:
            await _unavailable_card(update, context, edit=edit,
                                    edit_message_id=edit_message_id)
            return
        page_tracks, total = data
        if total == 0:
            if source == "often":
                # M07 — the derived view is empty until re-requests happen
                await _show_text(update, context, copy.LIB_OFTEN_EMPTY,
                                 keyboards.library_often_empty_keyboard(),
                                 edit=edit,
                                 edit_message_id=edit_message_id)
            else:  # latest / browse — the library itself is empty
                await _show_text(update, context, copy.LIB_NONE,
                                 keyboards.library_empty_keyboard(),
                                 edit=edit,
                                 edit_message_id=edit_message_id)
            return
        pages = math.ceil(total / library.LIB_PAGE_SIZE)
    token = _put_list_snapshot(
        context, tg_id, source, sort, query, page, pages, total,
        frozen_tracks if source == "search" else page_tracks)
    if source == "search":
        start = (page - 1) * library.LIB_PAGE_SIZE + 1
        text = library.library_search_results_caption(
            query or "", page, pages, total, page_tracks)
        markup = keyboards.library_search_results_keyboard(
            token, page=page, pages=pages, count=len(page_tracks),
            start=start)
    else:
        text = library.library_list_caption(
            _list_head(source, sort), total, page, pages, page_tracks)
        markup = keyboards.library_list_keyboard(
            token, source=source, sort=sort, page=page, pages=pages,
            count=len(page_tracks))
    await _show_text(update, context, text, markup,
                     edit=edit, edit_message_id=edit_message_id)


async def _render_detail(update: Update, context: MyContext, entry: dict, *,
                         edit: bool):
    """M05 — the track detail for a frozen detail snapshot (the entry
    carries its own `token` — set by the caller, not the store)."""
    track: LibraryTrack = entry["track"]
    await _show_text(
        update, context,
        library.library_detail_caption(track),
        keyboards.library_detail_keyboard(entry["token"],
                                          spotify_url=track.spotify_url),
        edit=edit)


async def _render_confirm(update: Update, context: MyContext, entry: dict, *,
                          edit: bool):
    """M05b — remove confirmation (edits the detail card in place; the
    detail snapshot keeps serving the confirm's buttons until TTL)."""
    track: LibraryTrack = entry["track"]
    text = copy.LIB_REMOVE_CONFIRM.format(
        title=escape_html(track.title),
        artist=escape_html(track.artist),
    )
    await _show_text(update, context, text,
                     keyboards.library_remove_confirm_keyboard(entry["token"]),
                     edit=edit)


async def _render_sort(update: Update, context: MyContext, *, edit: bool):
    """M04 — Browse all sort selection (stateless callbacks)."""
    text = "\n\n".join([copy.LIB_BROWSE_TITLE, copy.LIB_SORT_PROMPT])
    await _show_text(update, context, text,
                     keyboards.library_sort_keyboard(), edit=edit)


async def _render_origin(update: Update, context: MyContext,
                         detail_entry: dict, *, edit: bool):
    """Back to the list the detail was opened from — re-read fresh so
    a removed row disappears and a now-dead page falls back to 1 (the
    origin context rides in the snapshot, design §5.1)."""
    origin = detail_entry["origin"]
    await _render_list(
        update, context, detail_entry["tg_id"],
        origin["source"], sort=origin.get("sort"),
        query=origin.get("query"), page=origin.get("page", 1), edit=edit,
    )


async def _origin_markup(context: MyContext, detail_entry: dict):
    """The ORIGIN list's keyboard for re-sent audio from a detail card
    (plan §12.1.4): a fresh read of the origin list gets a fresh
    token, so the ▶/⋯ rows keep working from the delivered message.
    If the list is gone (rows removed, DB down) — or the origin was a
    search result set whose full freeze is no longer held — the audio
    carries the menu keyboard instead: never a dead token."""
    origin = detail_entry["origin"]
    if origin["source"] == "search":
        return keyboards.library_menu_keyboard()
    tg_id = detail_entry["tg_id"]
    source, sort = origin["source"], origin.get("sort")
    first = await library.library_page(tg_id, source, sort=sort, page=1)
    if first is None or first[1] == 0:
        return keyboards.library_menu_keyboard()
    total = first[1]
    pages = math.ceil(total / library.LIB_PAGE_SIZE)
    page = min(origin.get("page", 1), pages)
    if page == 1:
        page_tracks = first[0]
    else:
        page_tracks = (await library.library_page(
            tg_id, source, sort=sort, page=page))[0]
    token = _put_list_snapshot(context, tg_id, source, sort, None,
                               page, pages, total, page_tracks)
    return _list_markup(token, {
        "source": source, "sort": sort,
        "page": page, "pages": pages, "tracks": page_tracks,
    })


# --- "Get again": the cloud re-send ladder (plan §3) ----------------------------

async def _flood_card(update: Update, context: MyContext, err, cq) -> None:
    """429 on a single re-send (plan §12.2): visible wait with the
    dynamic seconds; the wait card's Try again IS the tapped payload,
    so re-tapping early just gets another honest 429 — never a
    bypass."""
    assert update.effective_chat is not None
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=copy.LIB_FLOOD_WAIT.format(seconds=_flood_wait_seconds(err)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.library_flood_wait_keyboard(cq.data),
    )


async def _redeliver(cq, update: Update, context: MyContext, markup,
                     track: LibraryTrack) -> None:
    """Re-deliver one library track (the plan §3 ladder — semantics
    unchanged; the track is always a FRESH row read, plan §12.4).

    1. sendAudio by file_id — the documented cloud re-send: Telegram
       relays the stored file server-side, no upload from our server,
       no 50 MB limit. The audio keeps its baked-in title/artist/cover;
       no thumbnail parameter (thumbnails can't be re-sent).
    2. copyMessage of the original delivery — only when step 1 failed
       and the original message refs are known.
    3. Full re-download via the Spotify URL (the existing task flow) —
       its delivery capture refreshes this row's cloud handles.
    4. Exhausted: an honest failure card (spec §7 class 2), never a
       silent skip.
    """
    assert update.effective_chat is not None
    chat_id = update.effective_chat.id
    tg_id = update.effective_user.id

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
            await _flood_card(update, context, err, cq)
            return
        except TelegramError:
            logger.warning(
                "library re-send by file_id failed for %r; "
                "trying copyMessage", track.track_key, exc_info=True,
            )
        else:
            library.record_request(tg_id, track.track_key)
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
            await _flood_card(update, context, err, cq)
            return
        except TelegramError:
            # the original message is gone (deleted chat, purged) — the
            # re-download is the only honest left
            logger.warning(
                "library copyMessage failed for %r; "
                "falling back to re-download", track.track_key, exc_info=True,
            )
        else:
            library.record_request(tg_id, track.track_key)
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
    await context.bot.send_message(
        chat_id=chat_id,
        text=copy.LIB_NO_RESEND,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.library_no_resend_keyboard(),
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


async def _fresh_track(update: Update, context: MyContext,
                       track_key: str) -> LibraryTrack | None:
    """Plan §12.4: act on a fresh row, never on the snapshot's copy
    (a parallel delivery may have captured handles moments ago; a
    deleted row yields the not-found toast)."""
    return await library.get_library_track(update.effective_user.id,
                                           track_key)


def _with_token(entry: dict, token: str) -> dict:
    """The store maps token -> entry; entries don't carry their own
    token — handlers attach it for the render/markup helpers."""
    return dict(entry, token=token)


# --- callback handlers (wired in bot/handlers/callbacks.py) ---------------------

async def on_library_callback(update: Update, context: MyContext):
    """`📚 My music` (nav:library) — the shared chain, every state.

    Private chats only (plan §3 group safety). Returns None: opening
    the library changes no input mode.
    """
    cq = update.callback_query
    assert cq is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    await answer_callback(cq)
    await _render_menu(update, context, update.effective_user.id, edit=True)


async def on_lib_destination(update: Update, context: MyContext):
    """Stateless menu destinations — `lib:latest` (M02), `lib:often`
    (M02 or M07) and `lib:browse` (M04). Re-read the DB fresh; the
    menu itself never stores a snapshot."""
    cq = update.callback_query
    assert cq is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    await answer_callback(cq)
    tg_id = update.effective_user.id
    data = (cq.data or "").strip()
    if data == "lib:latest":
        await _render_list(update, context, tg_id, "latest", edit=True)
    elif data == "lib:often":
        await _render_list(update, context, tg_id, "often", edit=True)
    else:  # lib:browse
        await _render_sort(update, context, edit=True)


async def on_lib_sort(update: Update, context: MyContext):
    """`lib:sort:<recent|added|az>` — M02 browse page 1 in that order
    (the sort is reflected in the list heading, design §4.5)."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_SORT_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    await answer_callback(cq)
    await _render_list(update, context, update.effective_user.id,
                       "browse", sort=match.group(1), edit=True)


async def on_lib_page(update: Update, context: MyContext):
    """`lib:page:<source>:<n>` — Previous/Next on browse-family lists
    (Latest/Often/Browse ×3). Re-reads the DB fresh; a page that no
    longer exists (rows removed since render) gets the stale-page
    toast, page < 1 falls back to page 1."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_PAGE_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    source, sort = _PAGE_SOURCES[match.group(1)]
    page = max(1, int(match.group(2)))
    tg_id = update.effective_user.id

    first = await library.library_page(tg_id, source, sort=sort, page=1)
    if first is None:
        await answer_callback(cq, copy.TOAST_LIB_FAILED)
        return
    total = first[1]
    pages = math.ceil(total / library.LIB_PAGE_SIZE) if total else 1
    if page > pages:
        await answer_callback(cq, copy.TOAST_LIB_PAGE)
        return
    await answer_callback(cq)
    await _render_list(update, context, tg_id, source, sort=sort,
                       page=page, edit=True)


async def on_lib_search_page(update: Update, context: MyContext):
    """`lib:spg:<token>:<n>` — Previous/Next inside a frozen search
    result set: re-renders page n FROM THE FREEZE (no DB read —
    design §6.6: never reinterpret an old button against a new
    result set)."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_SEARCH_PAGE_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    token, page_s = match.group(1), int(match.group(2))
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    entry = get_token_store(context, LIB_STORE).get(token)
    if (entry is None or entry["tg_id"] != update.effective_user.id
            or entry.get("source") != "search"):
        await answer_callback(cq, copy.TOAST_LIB_STALE)
        return
    page = max(1, page_s)
    if page > entry["pages"]:
        await answer_callback(cq, copy.TOAST_LIB_PAGE)
        return
    await answer_callback(cq)
    await _render_list(
        update, context, entry["tg_id"], "search",
        query=entry.get("query"), page=page, edit=True,
        frozen_tracks=entry["tracks"],
    )


async def on_lib_row(update: Update, context: MyContext):
    """`lib:get:<T>:<i>` (▶ i, re-send) and `lib:more:<T>:<i>` (⋯ i,
    track detail) — the numbered row actions of M02/M06. i is the
    1-based row of the token's frozen page; an expired/unknown token,
    a foreign owner or an out-of-range index answers the stale toast
    and runs nothing (design §9.4)."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_ROW_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    action, token, idx_s = match.group(1), match.group(2), match.group(3)
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    entry = get_token_store(context, LIB_STORE).get(token)
    if (entry is None or entry["tg_id"] != update.effective_user.id
            or entry.get("source") == "detail"):
        await answer_callback(cq, copy.TOAST_LIB_STALE)
        return
    rows = _page_rows(entry)
    index = int(idx_s)
    if not 1 <= index <= len(rows):
        await answer_callback(cq, copy.TOAST_LIB_STALE)
        return
    track = await _fresh_track(update, context, rows[index - 1].track_key)
    if track is None:
        await answer_callback(cq, copy.TOAST_LIB_NOT_FOUND)
        return
    if action == "get":
        await answer_callback(cq)
        await _redeliver(cq, update, context, _list_markup(token, entry),
                         track)
    else:  # more -> M05 (the detail snapshot carries the origin list)
        await answer_callback(cq)
        origin = _origin_from_list(entry)
        detail_token = _put_detail_snapshot(
            context, entry["tg_id"], track, origin)
        detail_entry = _with_token(
            get_token_store(context, LIB_STORE).get(detail_token),
            detail_token)
        await _render_detail(update, context, detail_entry, edit=True)


async def on_lib_detail(update: Update, context: MyContext):
    """`lib:<dl|pv|rm|rmc|keep|back>:<D>` — the M05/M05b actions on a
    frozen detail snapshot (D names the track; the card IS the track,
    so no index in the payload)."""
    cq = update.callback_query
    assert cq is not None
    match = re.match(LIB_DETAIL_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    action, token = match.group(1), match.group(2)
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    entry = get_token_store(context, LIB_STORE).get(token)
    if (entry is None or entry["tg_id"] != update.effective_user.id
            or entry.get("source") != "detail"):
        await answer_callback(cq, copy.TOAST_LIB_STALE)
        return
    entry = _with_token(entry, token)
    track_key = entry["track_key"]

    if action == "rm":
        await answer_callback(cq)
        await _render_confirm(update, context, entry, edit=True)
        return
    if action == "keep":
        await answer_callback(cq)
        await _render_detail(update, context, entry, edit=True)
        return
    if action == "back":
        await answer_callback(cq)
        await _render_origin(update, context, entry, edit=True)
        return
    if action == "dl":
        await answer_callback(cq)
        track = await _fresh_track(update, context, track_key)
        if track is None:
            await answer_callback(cq, copy.TOAST_LIB_NOT_FOUND)
            return
        await _redeliver(cq, update, context,
                         await _origin_markup(context, entry), track)
        return
    if action == "pv":
        track = await _fresh_track(update, context, track_key)
        if track is None:
            await answer_callback(cq, copy.TOAST_LIB_NOT_FOUND)
            return
        await _send_preview(cq, update, context, track)
        return
    # rmc — confirmed removal: no early ack, the toast reports the
    # real outcome; success restores the origin list
    ok = await library.remove_track(entry["tg_id"], track_key)
    if not ok:
        await answer_callback(cq, copy.TOAST_LIB_FAILED)
        return
    await answer_callback(cq, copy.TOAST_LIB_REMOVED)
    await _render_origin(update, context, entry, edit=True)


# --- library search (M03 state, plan §12.6) -------------------------------------

async def _search_prompt(update: Update, context: MyContext, *,
                         edit: bool,
                         message: str | None = None) -> None:
    """M03 — the search-prompt card (re-rendered with the too-short
    line when the query is <2 chars). Remembers its message id in
    user_data so /cancel can edit it back into the menu; a re-render
    from a TEXT update (no callback) edits that stored id in place."""
    text = "\n\n".join([
        message if message is not None else copy.LIB_SEARCH_TITLE,
        copy.LIB_SEARCH_PROMPT,
        copy.LIB_SEARCH_HINT,
        copy.LIB_SEARCH_CANCEL_HINT,
    ])
    chat = update.effective_chat
    assert chat is not None
    ref = context.user_data.get(LIB_SEARCH_CARD_KEY)
    edit_message_id = ref[1] if (edit and ref is not None) else None
    message_id = await _show_text(update, context, text,
                                  keyboards.library_search_prompt_keyboard(),
                                  edit=edit, edit_message_id=edit_message_id)
    if message_id is not None:
        context.user_data[LIB_SEARCH_CARD_KEY] = (chat.id, message_id)


def _cancel_search_card(context: MyContext) -> tuple | None:
    return context.user_data.pop(LIB_SEARCH_CARD_KEY, None)


async def on_lib_search_start(update: Update, context: MyContext):
    """`lib:search` — M01/M07 → M03: enter the search state. The
    returned state constant is what the ConversationHandler records,
    so the next text message routes to `on_lib_search_text`."""
    cq = update.callback_query
    assert cq is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return HOME_STATE
    await answer_callback(cq)
    await _search_prompt(update, context, edit=True)
    return LIBRARY_SEARCH_STATE


async def on_lib_search_new(update: Update, context: MyContext):
    """`lib:scn` — 🔎 New search / 🔎 Search again (M06) → M03 again."""
    cq = update.callback_query
    assert cq is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return HOME_STATE
    await answer_callback(cq)
    await _search_prompt(update, context, edit=True)
    return LIBRARY_SEARCH_STATE


async def on_lib_search_cancel(update: Update, context: MyContext):
    """`lib:scx` — ✖ Cancel on the M03 card: toast, back to the menu,
    state → Home."""
    cq = update.callback_query
    assert cq is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return HOME_STATE
    _cancel_search_card(context)
    await answer_callback(cq, copy.TOAST_LIB_SEARCH_CANCELLED)
    await _render_menu(update, context, update.effective_user.id, edit=True)
    return HOME_STATE


async def on_lib_search_cancel_command(update: Update, context: MyContext):
    """/cancel inside the search state (plan §12.6): there, /cancel
    means "stop searching" — the state chain registers it BEFORE the
    shared task-cancel handler."""
    assert update.effective_message is not None
    chat = update.effective_chat
    if chat is None or chat.type != "private":
        await update.effective_message.reply_text(copy.TOAST_LIB_PRIVATE)
        return HOME_STATE
    ref = _cancel_search_card(context)
    await _render_menu(update, context, update.effective_user.id,
                       edit=True,
                       edit_message_id=ref[1] if ref else None)
    await update.effective_message.reply_text(
        copy.TOAST_LIB_SEARCH_CANCELLED,
        parse_mode=ParseMode.HTML,
    )
    return HOME_STATE


async def on_lib_search_text(update: Update, context: MyContext):
    """The search state's TEXT handler (plan §12.6): the message is a
    library query. Media links never reach here — the state chain's
    wrapped link handler hands them to the download flow first (owner
    decision §12.1.1). <2 chars re-renders the prompt with the
    too-short line (stays in state); a query renders M06 (results or
    no-results) and ends the state — the result buttons then run on
    the shared chain, exactly like the Spotify search card."""
    assert update.effective_message is not None
    assert update.effective_user is not None
    chat = update.effective_chat
    if chat is None or chat.type != "private":
        await update.effective_message.reply_text(copy.TOAST_LIB_PRIVATE)
        return HOME_STATE

    raw = (update.effective_message.text or "").strip()
    query_norm = library.normalize_query(raw)
    if len(query_norm) < library.LIB_SEARCH_MIN_CHARS:
        await _search_prompt(update, context, edit=True,
                             message=copy.LIB_SEARCH_TOO_SHORT)
        return LIBRARY_SEARCH_STATE

    tg_id = update.effective_user.id
    results = await library.search_library(tg_id, raw)
    ref = _cancel_search_card(context)
    if results is None:
        await _unavailable_card(update, context, edit=True,
                                edit_message_id=ref[1] if ref else None)
        return HOME_STATE
    # results (or the no-results card, for an empty result set):
    # _render_list stores the FULL set in the snapshot — paging slices
    # from the freeze. The RAW query rides the snapshot for display.
    # edit_message_id targets the prompt card (a TEXT update carries no
    # callback — the stored id is the only handle to it).
    await _render_list(update, context, tg_id, "search", query=raw,
                       page=1, edit=True,
                       edit_message_id=ref[1] if ref else None,
                       frozen_tracks=results)
    return HOME_STATE


# --- legacy v2 payloads (defensive, plan §12.3) ---------------------------------

async def on_lib_legacy(update: Update, context: MyContext):
    """Pre-v3 library payloads (v2 cards that may still sit in the
    live chat): unresolvable by design -> the stale toast, same as
    the song-card legacy (spec §16)."""
    cq = update.callback_query
    assert cq is not None
    if not _private_ok(update):
        await answer_callback(cq, copy.TOAST_LIB_PRIVATE)
        return
    await answer_callback(cq, copy.TOAST_LIB_STALE)
