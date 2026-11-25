from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.playlist import Playlist
from models.playlist_item import PlaylistItem
from models.tag import Tag
from models.track import Track
from models.track_tag import TrackTag


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


def all_tracks(db: Session, tg_id: int):
    """Every row of the user's library, stable order (plan §12.5).

    v3 filters/sorts/paginates in Python (Unicode casefolding,
    collation-independent) instead of per-source SQL orderings — the
    per-user set is a few thousand rows max on a local Postgres.
    """
    return (
        db.query(Track)
        .filter(Track.tg_id == tg_id)
        .order_by(Track.id)
        .all()
    )


def get_track(db: Session, tg_id: int, track_key: str) -> Track | None:
    """One row by dedup key (plan §12.4: re-send/preview act on a
    FRESH row read, never on the snapshot's possibly-stale copy)."""
    return (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .first()
    )


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
    """Delete the row (playlist items and tag links cascade — spec §4.1
    orphan pruning). True when a row went."""
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
    """Create a playlist; None when the name is already taken
    (case-insensitive unique, spec §4.1)."""
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
        touch_playlist(db, playlist)
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
    touch_playlist(db, playlist)
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


def list_playlists_with_counts(db: Session, tg_id: int):
    """Every playlist of the user as (Playlist, track_count), newest
    first (WebApp v2 §4.2). The count is a LEFT JOIN aggregate — an
    empty playlist reads 0, not missing."""
    counts = (
        db.query(PlaylistItem.playlist_id, func.count(PlaylistItem.track_id))
        .group_by(PlaylistItem.playlist_id)
        .all()
    )
    count_by_id = dict(counts)
    playlists = (
        db.query(Playlist)
        .filter(Playlist.tg_id == tg_id)
        .order_by(Playlist.created_on.desc(), Playlist.id.desc())
        .all()
    )
    return [(p, count_by_id.get(p.id, 0)) for p in playlists]


def touch_playlist(db: Session, playlist: Playlist) -> None:
    playlist.updated_on = datetime.now()


def add_playlist_tracks(
    db: Session, tg_id: int, playlist_id: int, track_keys: list[str]
) -> tuple[int, int]:
    """Append several tracks at the end (WebApp v2 §4.2). Idempotent: an
    existing membership is skipped, not an error. Positions within the
    batch follow the request's key order. Returns
    (added, unknown) — unknown = keys with no library row."""
    playlist = get_playlist(db, tg_id, playlist_id)
    if playlist is None:
        return 0, len(track_keys)
    tracks = (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key.in_(track_keys))
        .all()
    )
    known = {t.track_key for t in tracks}
    unknown = len([k for k in track_keys if k not in known])
    # the client's request order is the playlist order (the query above
    # returns rows in no guaranteed order); dedupe first so a repeated
    # key keeps its FIRST position
    order = {k: i for i, k in enumerate(dict.fromkeys(track_keys))}
    tracks.sort(key=lambda t: order.get(t.track_key, len(order)))
    existing = {
        row.track_id
        for row in db.query(PlaylistItem.track_id)
        .filter(PlaylistItem.playlist_id == playlist_id)
        .all()
    }
    max_position = (
        db.query(func.max(PlaylistItem.position))
        .filter(PlaylistItem.playlist_id == playlist_id)
        .scalar()
    ) or 0
    added = 0
    for track in tracks:
        if track.id in existing:
            continue
        max_position += 1
        db.add(PlaylistItem(
            playlist_id=playlist_id, track_id=track.id, position=max_position,
        ))
        existing.add(track.id)
        added += 1
    if added:
        touch_playlist(db, playlist)
    db.commit()
    return added, unknown


def remove_playlist_item_by_key(
    db: Session, tg_id: int, playlist_id: int, track_key: str
) -> bool:
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
    deleted = (
        db.query(PlaylistItem)
        .filter(
            PlaylistItem.playlist_id == playlist_id,
            PlaylistItem.track_id == track.id,
        )
        .delete()
    )
    if deleted:
        touch_playlist(db, playlist)
    db.commit()
    return deleted > 0


def playlist_touch_on_change(db: Session, tg_id: int, playlist_id: int) -> None:
    playlist = get_playlist(db, tg_id, playlist_id)
    if playlist is not None:
        touch_playlist(db, playlist)
        db.commit()


# --- tags (WebApp v2 §4.1/§4.2) ----------------------------------------------

def create_tag(db: Session, tg_id: int, name: str):
    """Create a tag; None when the name is already taken (case-
    insensitive unique)."""
    tag = Tag(tg_id=tg_id, name=name)
    db.add(tag)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(tag)
    return tag


def find_tag(db: Session, tg_id: int, name: str):
    """Case-insensitive lookup by name — the SAME `lower()` the unique
    constraint uses, so the lookup and the constraint can never
    disagree (casefold() differs from lower() on some Unicode, e.g. ß)."""
    return (
        db.query(Tag)
        .filter(
            Tag.tg_id == tg_id,
            func.lower(Tag.name) == name.lower(),
        )
        .first()
    )


def list_tags_with_counts(db: Session, tg_id: int):
    """Every tag of the user as (Tag, link_count), name-ordered (A→Z on
    the casefolded name — the chips render in that order)."""
    counts = (
        db.query(TrackTag.tag_id, func.count(TrackTag.track_key))
        .group_by(TrackTag.tag_id)
        .all()
    )
    count_by_id = dict(counts)
    tags = (
        db.query(Tag)
        .filter(Tag.tg_id == tg_id)
        .order_by(func.lower(Tag.name), Tag.id)
        .all()
    )
    return [(t, count_by_id.get(t.id, 0)) for t in tags]


def delete_tag(db: Session, tg_id: int, tag_id: int) -> bool:
    """Delete a tag; its track links cascade (the spec's orphan
    pruning). True when a tag went."""
    tag = (
        db.query(Tag)
        .filter(Tag.tg_id == tg_id, Tag.id == tag_id)
        .first()
    )
    if tag is None:
        return False
    db.delete(tag)
    db.commit()
    return True


def track_tag_names(db: Session, tg_id: int, track_key: str) -> list[str]:
    """The tags on one track, name-ordered (the track sheet's chip row)."""
    rows = (
        db.query(Tag)
        .join(TrackTag, TrackTag.tag_id == Tag.id)
        .filter(
            TrackTag.tg_id == tg_id,
            TrackTag.track_key == track_key,
            Tag.tg_id == tg_id,
        )
        .order_by(func.lower(Tag.name), Tag.id)
        .all()
    )
    return [t.name for t in rows]


def track_tags_map(db: Session, tg_id: int, track_keys: list[str]) -> dict:
    """tags for many tracks in ONE query (WebApp v2 §4.2): the library
    and playlist pages embed each track's chip row in its JSON. Keys
    with no links map to [] (dict.setdefault on the fly)."""
    out: dict = {}
    if not track_keys:
        return out
    rows = (
        db.query(TrackTag.track_key, Tag.name)
        .join(Tag, Tag.id == TrackTag.tag_id)
        .filter(
            TrackTag.tg_id == tg_id,
            Tag.tg_id == tg_id,
            TrackTag.track_key.in_(track_keys),
        )
        .order_by(func.lower(Tag.name), Tag.id)
        .all()
    )
    for key, name in rows:
        out.setdefault(key, []).append(name)
    return out


def set_track_tags(
    db: Session, tg_id: int, track_key: str, names: list[str]
) -> list[str]:
    """Merge the given names onto one track (WebApp v2 §4.2 "set/merge"):
    each name is matched case-insensitively against the user's tags and
    created when missing; existing links are kept. Returns the track's
    full resulting tag-name list (name-ordered)."""
    track = (
        db.query(Track)
        .filter(Track.tg_id == tg_id, Track.track_key == track_key)
        .first()
    )
    if track is None:
        return []
    linked_ids: set[int] = {
        row.tag_id
        for row in db.query(TrackTag.tag_id)
        .filter(
            TrackTag.tg_id == tg_id,
            TrackTag.track_key == track_key,
        )
        .all()
    }
    for name in names:
        tag = find_tag(db, tg_id, name)
        if tag is None:
            tag = create_tag(db, tg_id, name)
            if tag is None:  # raced with a case-variant insert — re-find
                tag = find_tag(db, tg_id, name)
        if tag is not None and tag.id not in linked_ids:
            db.add(TrackTag(tg_id=tg_id, track_key=track_key, tag_id=tag.id))
            linked_ids.add(tag.id)
    db.commit()
    return track_tag_names(db, tg_id, track_key)


def remove_track_tag(db: Session, tg_id: int, track_key: str, name: str) -> bool:
    """Unlink one tag from one track (case-insensitive name match).
    True when a link went; False when there was none to remove (the
    endpoint stays idempotent — the UI can retry safely)."""
    rows = (
        db.query(TrackTag)
        .join(Tag, Tag.id == TrackTag.tag_id)
        .filter(
            TrackTag.tg_id == tg_id,
            TrackTag.track_key == track_key,
            Tag.tg_id == tg_id,
            func.lower(Tag.name) == name.strip().lower(),
        )
        .all()
    )
    if not rows:
        return False
    for row in rows:
        db.delete(row)
    db.commit()
    return True


def tagged_track_keys(db: Session, tg_id: int, tag_name: str) -> set[str]:
    """The track_keys carrying one tag (case-insensitive) — the
    `?tag=` filter for the library views. Empty set = the tag exists
    with no links or does not exist; the caller cannot tell and does
    not need to (both read as "no matches")."""
    rows = (
        db.query(TrackTag.track_key)
        .join(Tag, Tag.id == TrackTag.tag_id)
        .filter(
            Tag.tg_id == tg_id,
            TrackTag.tg_id == tg_id,
            func.lower(Tag.name) == tag_name.lower(),
        )
        .all()
    )
    return {row.track_key for row in rows}
