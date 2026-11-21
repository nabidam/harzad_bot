from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.playlist import Playlist
from models.playlist_item import PlaylistItem
from models.track import Track


# --- tracks -----------------------------------------------------------------

def upsert_track(
    db: Session,
    *,
    tg_id: int,
    track_key: str,
    title: str,
    artist: str = "",
    album: str = "",
    duration_sec: int | None = None,
    cover_url: str | None = None,
    spotify_url: str | None = None,
    source: str = "spotify",
    file_ref: tuple[str, int, int] | None = None,
) -> Track:
    """Insert-or-update the user's row for one delivered track.

    On update the metadata is refreshed (the delivery is the newest
    source of truth), `last_requested` moves to now and `request_count`
    increments. `file_ref = (file_id, chat_id, message_id)` is applied
    only when given — a delivery without a usable send response
    (in-flight timeout, plan §3) must NOT clear previously stored
    cloud handles.
    """
    now = datetime.now()
    row = (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .first()
    )
    if row is None:
        row = Track(tg_id=tg_id, track_key=track_key)
        db.add(row)
    row.title = title or ""
    row.artist = artist or ""
    row.album = album or ""
    row.duration_sec = duration_sec
    row.cover_url = cover_url
    row.spotify_url = spotify_url
    row.source = source
    row.last_requested = now
    row.request_count = (row.request_count or 0) + 1
    if file_ref is not None:
        row.file_id, row.orig_chat_id, row.orig_message_id = file_ref
    db.commit()
    db.refresh(row)
    return row


def backfill_track(
    db: Session,
    *,
    tg_id: int,
    track_key: str,
    title: str,
    artist: str = "",
    album: str = "",
    duration_sec: int | None = None,
    cover_url: str | None = None,
    spotify_url: str | None = None,
    source: str = "spotify",
    request_count: int = 1,
    added_on: datetime | None = None,
    last_requested: datetime | None = None,
) -> bool:
    """Insert-if-absent history row (log backfill, scripts/backfill_
    music_library.py).

    Never touches an existing row: a live delivery may have captured
    file refs + true counts that a log row cannot improve on. Timestamps
    come from the logs (when the user actually asked). False when the
    row already existed.
    """
    exists = (
        db.query(Track.id)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .first()
    )
    if exists is not None:
        return False
    now = datetime.now()
    db.add(Track(
        tg_id=tg_id,
        track_key=track_key,
        title=title,
        artist=artist or "",
        album=album or "",
        duration_sec=duration_sec,
        cover_url=cover_url,
        spotify_url=spotify_url,
        source=source,
        request_count=max(1, request_count),
        added_on=added_on or now,
        last_requested=last_requested or now,
    ))
    db.commit()
    return True


def recent_tracks(db: Session, tg_id: int, *, limit: int = 10, offset: int = 0):
    """The user's library, newest request first."""
    return (
        db.query(Track)
        .filter(Track.tg_id == tg_id)
        .order_by(Track.last_requested.desc(), Track.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_tracks(db: Session, tg_id: int) -> int:
    return (
        db.query(func.count(Track.id)).filter(Track.tg_id == tg_id).scalar() or 0
    )


def delete_track(db: Session, tg_id: int, track_key: str) -> bool:
    """Delete the row (playlist items cascade). True when a row went."""
    deleted = (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .delete()
    )
    db.commit()
    return deleted > 0


def touch_track(db: Session, tg_id: int, track_key: str) -> bool:
    """Count one more request and refresh `last_requested` (re-send)."""
    row = (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .first()
    )
    if row is None:
        return False
    row.last_requested = datetime.now()
    row.request_count = (row.request_count or 0) + 1
    db.commit()
    return True


# --- playlists ----------------------------------------------------------------

def create_playlist(db: Session, tg_id: int, name: str) -> Playlist | None:
    """Create a playlist; None when the name is already taken (unique)."""
    playlist = Playlist(tg_id=tg_id, name=name)
    db.add(playlist)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(playlist)
    return playlist


def list_playlists(db: Session, tg_id: int, limit: int = 10):
    return (
        db.query(Playlist)
        .filter(Playlist.tg_id == tg_id)
        .order_by(Playlist.created_on.desc(), Playlist.id.desc())
        .limit(limit)
        .all()
    )


def get_playlist(db: Session, tg_id: int, playlist_id: int) -> Playlist | None:
    return (
        db.query(Playlist)
        .filter(Playlist.tg_id == tg_id, Playlist.id == playlist_id)
        .first()
    )


def rename_playlist(db: Session, tg_id: int, playlist_id: int, name: str) -> bool:
    playlist = get_playlist(db, tg_id, playlist_id)
    if playlist is None:
        return False
    playlist.name = name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def delete_playlist(db: Session, tg_id: int, playlist_id: int) -> bool:
    deleted = (
        db.query(Playlist)
        .filter(Playlist.tg_id == tg_id, Playlist.id == playlist_id)
        .delete()
    )
    db.commit()
    return deleted > 0


def add_playlist_item(
    db: Session, tg_id: int, playlist_id: int, track_key: str
) -> bool:
    """Append a track at the next position. False when the playlist, the
    track, or an existing membership makes it a no-op (dedup)."""
    playlist = get_playlist(db, tg_id, playlist_id)
    if playlist is None:
        return False
    track = (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .first()
    )
    if track is None:
        return False
    exists = (
        db.query(PlaylistItem)
        .filter(
            PlaylistItem.playlist_id == playlist_id,
            PlaylistItem.track_id == track.id,
        )
        .first()
    )
    if exists is not None:
        return False
    max_position = (
        db.query(func.max(PlaylistItem.position))
        .filter(PlaylistItem.playlist_id == playlist_id)
        .scalar()
    ) or 0
    db.add(PlaylistItem(
        playlist_id=playlist_id, track_id=track.id, position=max_position + 1,
    ))
    db.commit()
    return True


def remove_playlist_item(
    db: Session, tg_id: int, playlist_id: int, track_id: int
) -> bool:
    playlist = get_playlist(db, tg_id, playlist_id)
    if playlist is None:
        return False
    deleted = (
        db.query(PlaylistItem)
        .filter(
            PlaylistItem.playlist_id == playlist_id,
            PlaylistItem.track_id == track_id,
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def playlist_tracks(db: Session, tg_id: int, playlist_id: int):
    """The playlist's tracks as (position, Track), in playback order."""
    playlist = get_playlist(db, tg_id, playlist_id)
    if playlist is None:
        return []
    rows = (
        db.query(PlaylistItem, Track)
        .join(Track, Track.id == PlaylistItem.track_id)
        .filter(
            PlaylistItem.playlist_id == playlist_id,
            Track.tg_id == tg_id,
        )
        .order_by(PlaylistItem.position)
        .all()
    )
    return [(item.position, track) for item, track in rows]


def playlist_track_count(db: Session, playlist_id: int) -> int:
    return (
        db.query(func.count(PlaylistItem.track_id))
        .filter(PlaylistItem.playlist_id == playlist_id)
        .scalar()
        or 0
    )
