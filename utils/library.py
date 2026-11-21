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
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger

import cruds.library as cruds
from connectors.db import get_db
from utils.decorators import (
    db_circuit_open,
    db_record_failure,
    db_record_success,
    spawn_db_task,
)
from utils.helpers import escape_html

logger = getLogger(__name__)

LIB_PAGE_SIZE = 10


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


# --- reads / removal (handler-side, soft-failing) -----------------------------

def read_library_sync(tg_id: int, page: int = 1,
                      page_size: int = LIB_PAGE_SIZE):
    """(tracks, total) for one page, newest request first. Raises on DB
    failure — the async wrapper turns that into None."""
    page = max(1, page)
    for db in get_db():
        total = cruds.count_tracks(db, tg_id)
        if page > ((total + page_size - 1) // page_size):
            page = 1  # stale page (rows removed since render) -> first page
        rows = cruds.recent_tracks(
            db, tg_id, limit=page_size,
            offset=(page - 1) * page_size,
        )
        return [_to_track(r) for r in rows], total


async def read_library(tg_id: int, page: int = 1,
                       page_size: int = LIB_PAGE_SIZE):
    """Read on a worker thread; None when the DB is open-circuited or
    the call fails (the handler renders the unavailable card)."""
    if db_circuit_open():
        return None
    try:
        result = await asyncio.to_thread(read_library_sync, tg_id, page,
                                         page_size)
    except Exception:
        logger.warning("music library read failed", exc_info=True)
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


# --- card rendering helpers ----------------------------------------------------

def relative_time(dt: datetime | None) -> str:
    """'2 d ago' for the card's 'last requested' line."""
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


def library_caption(track: LibraryTrack, index: int, total: int) -> str:
    """HTML caption for one library card (escape before calling is the
    caller's job for DB-stored values — they already came from the
    user's own requests, but the discipline is the same as cards)."""
    lines = [f"🎵 <b>{escape_html(track.title)}</b>"]
    if track.artist:
        lines.append(f"<i>{escape_html(track.artist)}</i>")
    meta = []
    if track.album:
        meta.append(f"📀 {escape_html(track.album)}")
    meta.append(f"📥 requested {track.request_count}× · "
                f"last {relative_time(track.last_requested)}")
    lines.append(" · ".join(meta))
    lines.append(f"Track {index} of {total}")
    return "\n".join(lines)
