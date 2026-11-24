"""Music library DB facade (docs/music-library-plan.md §2, §4).

Metadata only — no media bytes ever touch this box (root fs is nearly
full; the whole architecture is staging-delete). The re-send ladder
(plan §3) relies on the Telegram `file_id` captured at delivery, stored
per row.

Write contract (plan 1.6 / spec §4.1): delivery capture and request
counts are fire-and-forget background tasks guarded by the shared DB
circuit breaker — a DB outage must never break delivery. The read path
runs on a worker thread and returns None on failure; the handler shows
the honest "temporarily unavailable" card instead.
"""
import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from logging import getLogger

import cruds.library as cruds
from connectors.db import get_db
from utils import copy
from utils.decorators import (
    db_circuit_open,
    db_record_failure,
    db_record_success,
    spawn_db_task,
)
from utils.helpers import escape_html

logger = getLogger(__name__)

# 5 per list page (owner decision 2026-11-21: the My music list shows
# 5 tracks in full — fewer, complete rows beat a long thin one).
LIB_PAGE_SIZE = 5


@dataclass(frozen=True)
class LibraryTrack:
    """One library row, plain values (safe across the thread boundary)."""

    track_key: str
    title: str
    artist: str
    album: str
    duration_sec: int | None
    cover_url: str | None
    spotify_url: str | None
    source: str
    file_id: str | None
    orig_chat_id: int | None
    orig_message_id: int | None
    request_count: int
    added_on: datetime | None
    last_requested: datetime | None


def _to_track(row) -> LibraryTrack:
    return LibraryTrack(
        track_key=row.track_key,
        title=row.title,
        artist=row.artist or "",
        album=row.album or "",
        duration_sec=row.duration_sec,
        cover_url=row.cover_url,
        spotify_url=row.spotify_url,
        source=row.source,
        file_id=row.file_id,
        orig_chat_id=row.orig_chat_id,
        orig_message_id=row.orig_message_id,
        request_count=row.request_count or 0,
        added_on=row.added_on,
        last_requested=row.last_requested,
    )


def _g(obj, name, default=None):
    return getattr(obj, name, default)


def track_key_for_song(song) -> str | None:
    """Stable dedup key for a delivered spotdl Song (plan §4).

    Spotify track id first (attribute, then raw json); otherwise a
    sha1 of title|artist|album — still unique enough per user. None
    when nothing usable exists (the row is skipped, not faked).
    """
    song_id = _g(song, "song_id", None)
    if not song_id:
        raw = _g(song, "json", None) or {}
        song_id = raw.get("id") if isinstance(raw, dict) else None
    if song_id:
        return str(song_id)
    title = _g(song, "name", "") or ""
    artist = _g(song, "artist", "") or ""
    album = _g(song, "album_name", "") or ""
    if not (title or artist):
        return None
    return hashlib.sha1(
        f"{title}|{artist}|{album}".encode("utf-8")
    ).hexdigest()


def _song_fields(song) -> dict:
    raw = _g(song, "json", None) or {}

    def pick(attr, key, default=""):
        value = _g(song, attr, None)
        if value in (None, "") and isinstance(raw, dict):
            value = raw.get(key)
        return value or default

    return dict(
        title=pick("name", "name"),
        artist=pick("artist", "artist"),
        album=pick("album_name", "album"),
        duration_sec=_g(song, "duration", None),
        cover_url=_g(song, "cover_url", None),
        spotify_url=_g(song, "url", None),
        source="spotify",
    )


def _record_delivery_sync(tg_id: int, track_key: str, fields: dict,
                          file_ref: tuple | None) -> None:
    for db in get_db():
        cruds.upsert_track(
            db, tg_id=tg_id, track_key=track_key, file_ref=file_ref, **fields
        )


def _record_request_sync(tg_id: int, track_key: str) -> None:
    for db in get_db():
        cruds.touch_track(db, tg_id, track_key)


def record_song_delivery(tg_id: int, song, file_ref: tuple | None = None) -> None:
    """Fire-and-forget library upsert for one delivered song.

    `file_ref = (file_id, chat_id, message_id)` of the Telegram message
    that carried the audio, when the send returned a usable response
    (None for the in-flight-timeout case: the row is still recorded —
    metadata only, re-send falls back to the download ladder).

    Soft-failing: skipped while the circuit breaker is open, logged on
    error, never raised into the delivery path.
    """
    if db_circuit_open():
        logger.debug("library capture skipped: DB circuit open")
        return
    track_key = track_key_for_song(song)
    if not track_key:
        logger.debug("no stable track key for delivered song; no library row")
        return
    spawn_db_task(_record_delivery_sync, tg_id, track_key, _song_fields(song),
                  file_ref)


def record_request(tg_id: int, track_key: str) -> None:
    """Count one more request after a successful re-send (background)."""
    if db_circuit_open():
        return
    spawn_db_task(_record_request_sync, tg_id, track_key)


def _record_file_ref_sync(tg_id: int, track_key: str, fields: dict,
                          file_ref: tuple | None) -> None:
    for db in get_db():
        cruds.upsert_track(
            db, tg_id=tg_id, track_key=track_key, file_ref=file_ref, **fields
        )


def record_file_ref(tg_id: int, track_key: str, fields: dict,
                    file_ref: tuple | None) -> None:
    """Refresh a row's cloud handles after a re-download re-send (the
    Mini App ladder's step 3, plan §12.14).

    Upserts with the row's CURRENT metadata plus the fresh
    `(file_id, chat_id, message_id)`, so the request count and
    `last_requested` advance exactly as on a fresh delivery — the next
    re-send then takes the fast file_id path. `file_ref` is None in the
    in-flight-timeout case (the upsert then preserves the old handles,
    same contract as record_song_delivery). Soft-failing.
    """
    if db_circuit_open():
        logger.debug("library handle refresh skipped: DB circuit open")
        return
    spawn_db_task(_record_file_ref_sync, tg_id, track_key, fields, file_ref)


# --- reads (handler-side, soft-failing) ----------------------------------------

# M02 sources (plan §12.2) and Browse-all sorts (plan §12.4/§12.5).
SOURCES = ("latest", "search", "often", "browse")
SORTS = ("recent", "added", "az")
# "Often played" = requested more than once (design §7.1 selection rule).
OFTEN_MIN_COUNT = 2
LIB_SEARCH_MIN_CHARS = 2


def _ts(dt: datetime | None) -> datetime:
    return dt or datetime.min


def _norm(value: str | None) -> str:
    """Search normalization (plan §12.5): NFC + whitespace collapse +
    casefold — full Unicode casefolding, locale-independent."""
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", value or "")).strip().casefold()


def normalize_query(value: str | None) -> str:
    """Public for the search state handler (the too-short check)."""
    return _norm(value)


def _search_score(track: "LibraryTrack", query: str) -> int | None:
    """Relevance score (lower wins, plan §12.5): title prefix 0, title
    substring 1, artist prefix 2, artist substring 3, combined-only 4;
    None = no match. `query` must already be normalized."""
    title = _norm(track.title)
    artist = _norm(track.artist)
    if query in title:
        return 0 if title.startswith(query) else 1
    if query in artist:
        return 2 if artist.startswith(query) else 3
    if query in f"{title} {artist}":
        return 4
    return None


def _sort_tracks(rows: list[LibraryTrack], source: str,
                 sort: str | None, query: str | None) -> list[LibraryTrack]:
    """Filter + order one full user read (plan §12.5).

    Multi-key orders use successive stable sorts, least significant
    key first — deterministic without carrying the row id.
    """
    if source == "often":
        rows = [t for t in rows if t.request_count >= OFTEN_MIN_COUNT]
        rows.sort(key=lambda t: _norm(t.title))
        rows.sort(key=lambda t: _ts(t.last_requested), reverse=True)
        rows.sort(key=lambda t: t.request_count, reverse=True)
        return rows
    if source == "search":
        scores = {
            id(t): score
            for t in rows
            if (score := _search_score(t, query or "")) is not None
        }
        rows = [t for t in rows if id(t) in scores]
        rows.sort(key=lambda t: _norm(t.title))
        rows.sort(key=lambda t: _ts(t.last_requested), reverse=True)
        rows.sort(key=lambda t: t.request_count, reverse=True)
        rows.sort(key=lambda t: scores[id(t)])
        return rows
    if source == "browse":
        if sort == "added":
            rows.sort(key=lambda t: _norm(t.track_key))
            rows.sort(key=lambda t: _ts(t.added_on), reverse=True)
        elif sort == "az":
            rows.sort(key=lambda t: (_norm(t.title), _norm(t.artist),
                                     t.track_key))
        else:  # "recent" (default) — newest request first
            rows.sort(key=lambda t: _norm(t.track_key))
            rows.sort(key=lambda t: _ts(t.last_requested), reverse=True)
        return rows
    # "latest" (default): newest request first
    rows.sort(key=lambda t: _norm(t.track_key))
    rows.sort(key=lambda t: _ts(t.last_requested), reverse=True)
    return rows


def library_page_sync(tg_id: int, source: str = "latest", sort: str | None = None,
                      query: str | None = None, page: int = 1,
                      page_size: int = LIB_PAGE_SIZE):
    """(tracks, total) for one page of one source view (plan §12.5).

    One full user read on the calling thread; filter/sort/paginate in
    Python. Raises on DB failure — the async wrapper turns that into
    None. `page` past the end falls back to page 1 (rows may have been
    removed since the card rendered).
    """
    if source not in SOURCES:
        raise ValueError(f"unknown library source: {source!r}")
    if source == "browse" and sort not in (None, *SORTS):
        raise ValueError(f"unknown library sort: {sort!r}")
    page = max(1, page)
    if source == "search":
        query = _norm(query)
    for db in get_db():
        rows = [_to_track(r) for r in cruds.all_tracks(db, tg_id)]
    rows = _sort_tracks(rows, source, sort, query)
    total = len(rows)
    if page_size and total and page > ((total + page_size - 1) // page_size):
        page = 1
    start = (page - 1) * page_size
    return rows[start:start + page_size], total


async def library_page(tg_id: int, source: str = "latest", sort: str | None = None,
                       query: str | None = None, page: int = 1,
                       page_size: int = LIB_PAGE_SIZE):
    """Read on a worker thread; None when the DB is open-circuited or
    the call fails (the handler renders the unavailable card)."""
    if db_circuit_open():
        return None
    try:
        result = await asyncio.to_thread(
            library_page_sync, tg_id, source, sort, query, page, page_size)
    except Exception:
        logger.warning("music library read failed", exc_info=True)
        db_record_failure()
        return None
    db_record_success()
    return result


def search_library_sync(tg_id: int, query: str) -> list[LibraryTrack]:
    """The FULL matched + relevance-ordered result set (plan §12.5).

    The snapshot freezes this set and rendered pages slice from it, so
    result positions never shift under a rendered card (design §6.6).
    The query is normalized internally; fewer than the minimum chars
    matches nothing (the state handler re-renders the prompt instead).
    Raises on DB failure — the async wrapper turns that into None.
    """
    q = _norm(query)
    if len(q) < LIB_SEARCH_MIN_CHARS:
        return []
    for db in get_db():
        rows = [_to_track(r) for r in cruds.all_tracks(db, tg_id)]
    return _sort_tracks(rows, "search", None, q)


async def search_library(tg_id: int, query: str):
    """Worker-thread wrapper; None when the DB is open-circuited or the
    call fails (the handler renders the unavailable card)."""
    if db_circuit_open():
        return None
    try:
        result = await asyncio.to_thread(search_library_sync, tg_id, query)
    except Exception:
        logger.warning("music library search failed", exc_info=True)
        db_record_failure()
        return None
    db_record_success()
    return result


def get_library_track_sync(tg_id: int, track_key: str) -> LibraryTrack | None:
    for db in get_db():
        row = cruds.get_track(db, tg_id, track_key)
        return _to_track(row) if row is not None else None
    return None


async def get_library_track(tg_id: int, track_key: str) -> LibraryTrack | None:
    """Fresh row read for one track (plan §12.4): re-send/preview use
    this instead of the snapshot's copy — a parallel delivery may have
    just captured cloud handles; a deleted row yields None (the handler
    toasts not-found)."""
    if db_circuit_open():
        return None
    try:
        result = await asyncio.to_thread(get_library_track_sync, tg_id,
                                         track_key)
    except Exception:
        logger.warning("music library track read failed", exc_info=True)
        db_record_failure()
        return None
    db_record_success()
    return result


def remove_track_sync(tg_id: int, track_key: str) -> bool:
    for db in get_db():
        return cruds.delete_track(db, tg_id, track_key)
    return False


async def remove_track(tg_id: int, track_key: str) -> bool:
    """Delete a library row; False on DB failure (never raised)."""
    if db_circuit_open():
        return False
    try:
        result = await asyncio.to_thread(remove_track_sync, tg_id, track_key)
    except Exception:
        logger.warning("music library remove failed", exc_info=True)
        db_record_failure()
        return False
    db_record_success()
    return bool(result)


# --- daily view (owner add-on 2026-11-24: the Mini App's "Daily" menu) ---------

_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_label(day: date) -> str:
    """The Daily menu's row label: 'Today' / 'Yesterday' / 'Nov 18'
    (+ ', 2025' when the year differs). Server-local dates throughout
    (the stored timestamps are naive server-local)."""
    today = datetime.now().date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    label = f"{_MONTH_ABBR[day.month - 1]} {day.day}"
    if day.year != today.year:
        label += f", {day.year}"
    return label


def _track_day(track: "LibraryTrack") -> date | None:
    """The day the Daily view groups this row under: the local date of
    the LAST request — a re-request moves the track to the newer day.
    The library stores no per-request log (plan §12.14), so this is the
    only day signal per track; `added_on` is the fallback."""
    ts = track.last_requested or track.added_on
    return ts.date() if ts is not None else None


def daily_days_sync(tg_id: int) -> list[tuple[date, int]]:
    """`[(day, count)]`, newest first — one row per day with at least
    one requested track. Raises on DB failure (async wrapper -> None)."""
    for db in get_db():
        rows = [_to_track(r) for r in cruds.all_tracks(db, tg_id)]
    counts: dict[date, int] = {}
    for t in rows:
        d = _track_day(t)
        if d is not None:
            counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items(), reverse=True)


async def daily_days(tg_id: int):
    """Worker-thread wrapper; None when the DB is open-circuited or the
    call fails (the handler answers the unavailable state)."""
    if db_circuit_open():
        return None
    try:
        result = await asyncio.to_thread(daily_days_sync, tg_id)
    except Exception:
        logger.warning("music library daily read failed", exc_info=True)
        db_record_failure()
        return None
    db_record_success()
    return result


def daily_page_sync(tg_id: int, day: date, page: int = 1,
                    page_size: int = LIB_PAGE_SIZE):
    """(tracks, total) for one day's tracks, newest request first —
    the same order as the Latest view. `page` past the end falls back
    to page 1, like library_page_sync."""
    for db in get_db():
        rows = [_to_track(r) for r in cruds.all_tracks(db, tg_id)]
    rows = [t for t in rows if _track_day(t) == day]
    rows.sort(key=lambda t: _norm(t.track_key))
    rows.sort(key=lambda t: _ts(t.last_requested), reverse=True)
    total = len(rows)
    page = max(1, page)
    if page_size and total and page > ((total + page_size - 1) // page_size):
        page = 1
    start = (page - 1) * page_size
    return rows[start:start + page_size], total


async def daily_page(tg_id: int, day: date, page: int = 1,
                     page_size: int = LIB_PAGE_SIZE):
    """Worker-thread wrapper; None when the DB is open-circuited or the
    call fails (the handler answers the unavailable state)."""
    if db_circuit_open():
        return None
    try:
        result = await asyncio.to_thread(
            daily_page_sync, tg_id, day, page, page_size)
    except Exception:
        logger.warning("music library daily page read failed",
                       exc_info=True)
        db_record_failure()
        return None
    db_record_success()
    return result


# --- card rendering helpers (UX v3, plan §12.2) ---------------------------------

def _tracks_word(n: int) -> str:
    return "track" if n == 1 else "tracks"


def _track_lines(tracks: list[LibraryTrack],
                 start_index: int = 1) -> list[str]:
    """Numbered rows, one block per track (design §4.2): title line,
    artist · duration, request history. Album stays on the detail
    screen. DB-stored values are HTML-escaped here. `start_index`
    numbers the rows as their position in the SNAPSHOT's track list
    (search pages continue the numbering; the button index is the
    same number — that is what makes the tap unambiguous)."""
    lines: list[str] = []
    for index, track in enumerate(tracks, start_index):
        lines.append(f"{index}. <b>{escape_html(track.title)}</b>")
        parts: list[str] = []
        if track.artist:
            parts.append(f"<i>{escape_html(track.artist)}</i>")
        duration = duration_fmt(track.duration_sec)
        if duration:
            parts.append(duration)
        if parts:
            lines.append("   " + " · ".join(parts))
        requested = (copy.LIB_LIST_REQUESTED_ONCE
                     if track.request_count <= 1
                     else copy.LIB_LIST_REQUESTED.format(
                         count=track.request_count))
        lines.append(f"   {requested} · "
                     f"{copy.LIB_LIST_LAST.format(time=relative_time(track.last_requested))}")
        lines.append("")
    return lines


def library_list_caption(source_label: str, count: int, page: int,
                         pages: int, tracks: list[LibraryTrack]) -> str:
    """HTML for one M02 track list (latest / often / browse ×3).

    The page line only exists while there is more than one page
    (design §12.3 edge: don't force pagination on a 1-track library).
    """
    head = copy.LIB_LIST_TITLE.format(source=escape_html(source_label))
    count_line = copy.LIB_LIST_COUNT.format(count=count,
                                            word=_tracks_word(count))
    if pages > 1:
        count_line += copy.LIB_LIST_PAGE.format(page=page, pages=pages)
    lines = [head, count_line, ""]
    lines.extend(_track_lines(tracks))
    lines.append(copy.LIB_LIST_PROMPT)
    return "\n".join(lines).rstrip()


def library_search_results_caption(query: str, page: int, pages: int,
                                   total: int, tracks: list[LibraryTrack]) -> str:
    """HTML for one M06 search-results page (the query is user input —
    escaped before it reaches the template). `Found N tracks.` counts
    the whole result set (total, not the page) and joins the page
    position while paging."""
    found = copy.LIB_SEARCH_FOUND.format(count=total,
                                         word=_tracks_word(total))
    if pages > 1:
        found = found.rstrip(".") + copy.LIB_LIST_PAGE.format(
            page=page, pages=pages)
    lines = [
        copy.LIB_SEARCH_RESULTS.format(query=escape_html(query)),
        "",
        found,
        "",
    ]
    lines.extend(_track_lines(tracks, (page - 1) * LIB_PAGE_SIZE + 1))
    lines.append(copy.LIB_LIST_PROMPT)
    return "\n".join(lines).rstrip()


def library_search_none_caption(query: str) -> str:
    """HTML for the M06 zero-result state."""
    return "\n".join([
        copy.LIB_SEARCH_NONE,
        "",
        copy.LIB_SEARCH_NONE_DESC.format(query=escape_html(query)),
        "",
        copy.LIB_SEARCH_NONE_TRY,
    ])


def library_detail_caption(track: LibraryTrack) -> str:
    """HTML for the M05 track detail (design §5.1). Missing
    artist/album/duration omit their line, never render blanks."""
    lines = [copy.LIB_DETAIL_TITLE, "",
             f"<b>{escape_html(track.title)}</b>", ""]
    if track.artist:
        lines.append(copy.LIB_DETAIL_ARTIST.format(
            artist=escape_html(track.artist)))
    if track.album:
        lines.append(copy.LIB_DETAIL_ALBUM.format(
            album=escape_html(track.album)))
    duration = duration_fmt(track.duration_sec)
    if duration:
        lines.append(copy.LIB_DETAIL_DURATION.format(duration=duration))
    lines += [
        "",
        copy.LIB_DETAIL_REQUESTED.format(count=track.request_count),
        copy.LIB_DETAIL_LAST.format(time=relative_time(track.last_requested)),
        copy.LIB_DETAIL_ADDED.format(time=relative_time(track.added_on)),
        "",
        copy.LIB_DETAIL_PROMPT,
    ]
    return "\n".join(lines)


def relative_time(dt: datetime | None) -> str:
    """'2 d ago' for the list rows and detail screen."""
    if dt is None:
        return "—"
    seconds = max(0, int((datetime.now() - dt).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def duration_fmt(seconds: int | None) -> str:
    """236 -> '3:56' for the list rows."""
    if not seconds:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"
