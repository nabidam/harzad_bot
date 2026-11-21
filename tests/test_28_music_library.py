"""#28 Music library, Phase A (docs/music-library-plan.md).

- schema/CRUD live against the local Postgres (unique tg_ids, cleaned
  up in teardown — same contract as test_09): upsert dedup on
  (tg_id, track_key), file-ref preservation, ordering, cascades
- delivery capture: every successful Spotify delivery upserts a
  library row carrying the Telegram file_id (the cloud re-send
  handle); the in-flight timeout case records metadata only (plan §3
  known gap)
- utils.library facade: breaker-aware background writes, thread-based
  reads that degrade to None, relative-time + caption helpers
- the chat surface: nav:library render (empty / unavailable / data),
  private-only gate, tokenized card actions with owner checks and the
  stale toasts, and the Get-again re-send ladder (file_id ->
  copyMessage -> re-download -> honest exhausted answer)
- Home keyboard carries 📚 My music (plan §5); TrackResult carries
  the Spotify track id (plan §4 dedup key)
"""
import hashlib
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import text
from telegram.error import BadRequest, RetryAfter, TimedOut

import bot.handlers.music.library as lib_handler
import bot.handlers.music.spotify.download as download
import cruds.library as cruds
import connectors.spotify_search as spotify_search
import spotdl.utils.spotify as spotdl_spotify
import utils.decorators as decorators
import utils.library as library
from connectors.db import SessionLocal
from models.playlist import Playlist
from models.playlist_item import PlaylistItem
from models.track import Track
from utils import copy, keyboards
from utils.tokens import get_token_store

FIXTURES = Path(__file__).parent / "fixtures"
SMALL_MP3 = FIXTURES / "small.mp3"
TRACK_URL = "https://open.spotify.com/track/abc"

OWNER = 9_700_000_001
FOREIGN = 9_700_000_002
CHAT_ID = 7


# --- fixtures ------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _reset_db_breaker():
    """The DB breaker is a module singleton shared by all soft-failing
    DB work; isolate tests from each other's state (test_10 pattern)."""
    decorators._db_breaker.reset()
    yield
    await decorators.join_pending_db_work()
    decorators._db_breaker.reset()


def _fresh_tg_id() -> int:
    # tg_id is a 32-bit int in the schema: keep inside INT4 range
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def test_tg():
    tg_id = _fresh_tg_id()
    yield tg_id
    # teardown: drop everything this test created (items first, the
    # FK would cascade, but explicit is clearer)
    db = SessionLocal()
    db.execute(text("delete from logs where tg_id = :t"), {"t": tg_id})
    db.query(PlaylistItem).filter(
        PlaylistItem.track_id.in_(
            db.query(Track.id).filter(Track.tg_id == tg_id)
        )
    ).delete(synchronize_session=False)
    db.query(Playlist).filter(Playlist.tg_id == tg_id).delete(
        synchronize_session=False)
    db.query(Track).filter(Track.tg_id == tg_id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


# --- CRUD (live Postgres) --------------------------------------------------------

def test_upsert_creates_then_increments(db_session, test_tg):
    row = cruds.upsert_track(
        db_session, tg_id=test_tg, track_key="k1",
        title="T", artist="A", file_ref=("AgF1", 7, 100),
    )
    assert row.id is not None
    assert row.request_count == 1
    assert row.file_id == "AgF1"

    row = cruds.upsert_track(
        db_session, tg_id=test_tg, track_key="k1",
        title="T2", artist="A", file_ref=("AgF2", 7, 200),
    )
    assert row.request_count == 2
    assert row.file_id == "AgF2"
    assert row.orig_message_id == 200
    assert cruds.count_tracks(db_session, test_tg) == 1


def test_upsert_without_file_ref_keeps_handles(db_session, test_tg):
    """The in-flight case (no usable send response) must NOT clear the
    cloud handles stored by an earlier delivery."""
    cruds.upsert_track(
        db_session, tg_id=test_tg, track_key="k1",
        title="T", file_ref=("AgF1", 7, 100),
    )
    row = cruds.upsert_track(
        db_session, tg_id=test_tg, track_key="k1", title="T", album="B",
    )
    assert row.file_id == "AgF1"
    assert row.orig_chat_id == 7
    assert row.request_count == 2


def test_recent_tracks_newest_request_first(db_session, test_tg):
    for key in ("a", "b", "c"):
        cruds.upsert_track(db_session, tg_id=test_tg, track_key=key,
                           title=key)
    cruds.touch_track(db_session, test_tg, "a")
    keys = [t.track_key for t in cruds.recent_tracks(db_session, test_tg)]
    assert keys[0] == "a"
    assert set(keys) == {"a", "b", "c"}
    page2 = cruds.recent_tracks(db_session, test_tg, limit=2, offset=2)
    assert [t.track_key for t in page2] == keys[2:]


def test_delete_track_cascades_playlist_items(db_session, test_tg):
    cruds.upsert_track(db_session, tg_id=test_tg, track_key="k1",
                       title="T")
    pl = cruds.create_playlist(db_session, test_tg, "PL")
    assert cruds.add_playlist_item(db_session, test_tg, pl.id, "k1")
    assert cruds.playlist_track_count(db_session, pl.id) == 1

    assert cruds.delete_track(db_session, test_tg, "k1")
    assert cruds.playlist_track_count(db_session, pl.id) == 0
    assert cruds.delete_track(db_session, test_tg, "k1") is False


def test_playlist_crud_lifecycle(db_session, test_tg):
    pl = cruds.create_playlist(db_session, test_tg, "Road trip")
    assert pl is not None
    assert cruds.create_playlist(db_session, test_tg, "Road trip") is None
    for key in ("a", "b"):
        cruds.upsert_track(db_session, tg_id=test_tg, track_key=key,
                           title=key)

    assert cruds.add_playlist_item(db_session, test_tg, pl.id, "a")
    assert cruds.add_playlist_item(db_session, test_tg, pl.id, "b")
    assert cruds.add_playlist_item(db_session, test_tg, pl.id, "a") is False
    assert cruds.add_playlist_item(db_session, test_tg, pl.id,
                                   "missing") is False

    items = {t.track_key: pos
             for pos, t in cruds.playlist_tracks(db_session, test_tg,
                                                 pl.id)}
    assert items == {"a": 1, "b": 2}

    pl2 = cruds.create_playlist(db_session, test_tg, "Other")
    assert not cruds.rename_playlist(db_session, test_tg, pl.id,
                                     "Other")  # duplicate name
    assert cruds.rename_playlist(db_session, test_tg, pl.id, "Summer")
    assert sorted(p.name for p in cruds.list_playlists(db_session, test_tg)) \
        == ["Other", "Summer"]

    track_a = (
        db_session.query(Track)
        .filter_by(tg_id=test_tg, track_key="a").first()
    )
    assert cruds.remove_playlist_item(db_session, test_tg, pl.id,
                                      track_a.id)
    assert cruds.playlist_track_count(db_session, pl.id) == 1

    assert cruds.delete_playlist(db_session, test_tg, pl.id)
    assert [p.name for p in cruds.list_playlists(db_session, test_tg)] \
        == ["Other"]


def test_backfill_inserts_history_and_never_touches_existing(db_session, test_tg):
    """scripts/backfill_music_library.py writes through this crud: history
    timestamps/counts come from the logs; an existing row (live-captured
    file refs or an earlier backfill) is never modified."""
    first = datetime(2026, 10, 27, 9, 0, 0)
    last = datetime(2026, 11, 20, 9, 33, 0)
    assert cruds.backfill_track(
        db_session, tg_id=test_tg, track_key="bk1", title="T", artist="A",
        spotify_url="https://open.spotify.com/track/bk1",
        request_count=4, added_on=first, last_requested=last,
    )
    row = cruds.recent_tracks(db_session, test_tg)[0]
    assert row.request_count == 4
    assert row.added_on.replace(microsecond=0) == first
    assert row.last_requested.replace(microsecond=0) == last
    assert row.file_id is None

    # re-run: no-op, nothing shifts
    assert cruds.backfill_track(
        db_session, tg_id=test_tg, track_key="bk1", title="CHANGED",
        request_count=99,
    ) is False
    row = cruds.recent_tracks(db_session, test_tg)[0]
    assert row.title == "T" and row.request_count == 4

    # a live delivery row is authoritative: backfill must not touch it
    cruds.upsert_track(db_session, tg_id=test_tg, track_key="bk2",
                       title="L", file_ref=("AgF", 7, 100))
    assert cruds.backfill_track(
        db_session, tg_id=test_tg, track_key="bk2", title="L",
        request_count=9,
    ) is False
    rows = {t.track_key: t for t in cruds.recent_tracks(db_session, test_tg)}
    assert rows["bk2"].file_id == "AgF"
    assert rows["bk2"].request_count == 1


def test_collect_requests_parses_logs(db_session, test_tg):
    """The backfill script's log scan: track URLs deduped to
    (count, first, last), non-track Spotify URLs reported separately."""
    from sqlalchemy import text as sqltext
    import scripts.backfill_music_library as backfill

    rows = [
        (datetime(2026, 11, 1, 10, 0), "https://open.spotify.com/track/aaa?si=x"),
        (datetime(2026, 11, 2, 10, 0), "https://open.spotify.com/track/aaa?si=y"),
        (datetime(2026, 11, 3, 10, 0), "https://open.spotify.com/album/bbb?si=z"),
        (datetime(2026, 11, 4, 10, 0), "just a search query"),
    ]
    for ts, message in rows:
        db_session.execute(sqltext(
            "insert into logs (tg_id, message, created_on) "
            "values (:t, :m, :c)"),
            {"t": test_tg, "m": message, "c": ts})
    db_session.commit()

    requests, ignored = backfill.collect_requests(db_session, test_tg)
    assert set(requests) == {"aaa"}
    entry = requests["aaa"]
    assert entry["count"] == 2
    assert entry["url"] == "https://open.spotify.com/track/aaa"
    assert entry["first"] == rows[0][0]
    assert entry["last"] == rows[1][0]
    assert ignored == ["https://open.spotify.com/album/bbb?si=z"]


# --- utils.library core -----------------------------------------------------------

def test_track_key_prefers_spotify_id():
    song = SimpleNamespace(song_id="abc123", name="T", artist="A",
                           album_name="B")
    assert library.track_key_for_song(song) == "abc123"


def test_track_key_json_fallback_and_sha1():
    song = SimpleNamespace(name="T", artist="A", album_name="B",
                           json={"id": "j1"})
    assert library.track_key_for_song(song) == "j1"
    song2 = SimpleNamespace(name="T", artist="A", album_name="B")
    assert library.track_key_for_song(song2) == \
        hashlib.sha1(b"T|A|B").hexdigest()


def test_track_key_absent():
    assert library.track_key_for_song(
        SimpleNamespace(name="", artist="")) is None


async def test_record_song_delivery_upserts_row(test_tg):
    song = SimpleNamespace(
        song_id="s1", name="T", artist="A", album_name="B", duration=200,
        cover_url="http://c/x.jpg", url="https://open.spotify.com/track/s1",
    )
    library.record_song_delivery(test_tg, song, file_ref=("AgF", 7, 9))
    assert await decorators.join_pending_db_work()

    db = SessionLocal()
    rows = cruds.recent_tracks(db, test_tg)
    assert len(rows) == 1
    assert rows[0].title == "T"
    assert rows[0].file_id == "AgF"
    assert rows[0].orig_chat_id == 7
    assert rows[0].orig_message_id == 9

    # a second delivery (no file refs — in-flight style) increments and
    # keeps the earlier handles
    library.record_song_delivery(test_tg, song)
    await decorators.join_pending_db_work()
    db.expire_all()
    rows = cruds.recent_tracks(db, test_tg)
    assert len(rows) == 1
    assert rows[0].request_count == 2
    assert rows[0].file_id == "AgF"
    db.close()


async def test_record_skipped_while_breaker_open(test_tg, monkeypatch):
    monkeypatch.setattr(library, "db_circuit_open", lambda: True)
    song = SimpleNamespace(song_id="s1", name="T", artist="A",
                           album_name="B", duration=1, url="u")
    library.record_song_delivery(test_tg, song, file_ref=("AgF", 7, 9))
    assert not await decorators.join_pending_db_work()
    db = SessionLocal()
    assert cruds.count_tracks(db, test_tg) == 0
    db.close()


async def test_read_library_returns_none_on_failure(test_tg, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")
    monkeypatch.setattr(library, "read_library_sync", boom)
    assert await library.read_library(test_tg) is None
    assert decorators._db_breaker._consecutive_failures >= 1


async def test_read_library_pages_and_stale_page_fallback(test_tg):
    db = SessionLocal()
    for i in range(3):
        cruds.upsert_track(db, tg_id=test_tg, track_key=f"k{i}",
                           title=f"T{i}")
    db.close()

    tracks, total = await library.read_library(test_tg, 1)
    assert total == 3 and len(tracks) == 3
    # a stale page (rows removed since render) falls back to page 1
    tracks, total = await library.read_library(test_tg, 99)
    assert total == 3 and len(tracks) == 3
    tracks, total = await library.read_library(test_tg, 1, page_size=2)
    assert len(tracks) == 2


async def test_remove_track_async(test_tg):
    db = SessionLocal()
    cruds.upsert_track(db, tg_id=test_tg, track_key="k1", title="T")
    db.close()
    assert await library.remove_track(test_tg, "k1") is True
    assert await library.remove_track(test_tg, "k1") is False


def test_relative_time_and_caption():
    now = datetime.now()
    assert library.relative_time(now) == "just now"
    assert library.relative_time(now - timedelta(hours=5)) == "5 h ago"
    assert library.relative_time(now - timedelta(days=2)) == "2 d ago"
    assert library.relative_time(None) == "—"

    track = SimpleNamespace(
        title="T<b>", artist="A", album="B", request_count=3,
        last_requested=now,
    )
    caption = library.library_caption(track, 1, 10)
    assert "&lt;b&gt;" in caption  # escaped
    assert "Track 1 of 10" in caption
    assert "requested 3×" in caption


# --- delivery capture (flow level) --------------------------------------------------

class FakeSpotdl:
    """Single-song stand-in: sync search + async pool_download (the
    awaited-on-the-loop contract, like test_23's fake)."""

    def __init__(self, songs, tmp_path: Path):
        self._songs = list(songs)
        self._tmp = tmp_path

    def search(self, terms):
        return list(self._songs)

    @property
    def downloader(self):
        return SimpleNamespace(pool_download=self._pool_download)

    async def _pool_download(self, song):
        dest = self._tmp / f"{song.name}.mp3"
        shutil.copy(SMALL_MP3, dest)
        return song, dest


def make_song(name="T1", url=None, song_id="sid1"):
    return SimpleNamespace(
        name=name, artist="Artist", album_name="Album", duration=200,
        cover_url="http://cover/x.jpg",
        url=url or f"https://open.spotify.com/track/{name}",
        song_id=song_id, json={"id": song_id},
    )


def make_flow_context(fake, send_return=None):
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {"spotdl_instance": fake}
    edit = AsyncMock()
    status = MagicMock()
    status.message_id = 500
    status.delete = AsyncMock()
    status.edit_text = edit
    context.status_message = status
    context.bot = MagicMock()
    for m in ("send_chat_action", "send_audio", "send_photo",
              "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    if send_return is not None:
        context.bot.send_audio.return_value = send_return
    context.bot.edit_message_text = edit
    context.bot.send_message = AsyncMock(return_value=status)
    return context


def make_flow_update(url, tg_id):
    update = MagicMock()
    msg = MagicMock(chat_id=CHAT_ID, message_id=100, text=url,
                    reply_text=AsyncMock())
    update.message = msg
    update.effective_message = msg
    update.effective_chat = MagicMock(id=CHAT_ID)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    update.callback_query = None
    return update


@pytest.fixture
def cap_tg():
    """A tg_id whose library rows are cleaned up in teardown."""
    tg_id = _fresh_tg_id()
    yield tg_id
    db = SessionLocal()
    db.query(Track).filter(Track.tg_id == tg_id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


async def test_delivery_captures_file_refs(tmp_path, cap_tg):
    """A successful delivery stores the Telegram file_id + message refs
    (the cloud re-send ladder's primary handles)."""
    fake = FakeSpotdl([make_song()], tmp_path)
    context = make_flow_context(
        fake,
        send_return=SimpleNamespace(
            audio=SimpleNamespace(file_id="AgCloudFile"),
            chat_id=CHAT_ID, message_id=1234,
        ),
    )
    update = make_flow_update(TRACK_URL, cap_tg)

    await download.run_spotify_download(update, context, TRACK_URL)

    assert await decorators.join_pending_db_work()
    db = SessionLocal()
    rows = cruds.recent_tracks(db, cap_tg)
    assert len(rows) == 1
    assert rows[0].track_key == "sid1"  # Spotify track id is the key
    assert rows[0].file_id == "AgCloudFile"
    assert rows[0].orig_chat_id == CHAT_ID
    assert rows[0].orig_message_id == 1234
    assert rows[0].request_count == 1
    db.close()


async def test_in_flight_delivery_records_metadata_only(tmp_path, cap_tg):
    """Incident 2026-11-17 case: the send's response timed out after
    upload — no file_id exists, so the row is metadata-only and the
    re-send ladder falls back to the download (plan §3 known gap)."""
    song = make_song()
    fake = FakeSpotdl([song], tmp_path)
    context = make_flow_context(fake)
    err = TimedOut("Timed out")
    err.__cause__ = httpx.ReadTimeout("read timed out")
    context.bot.send_audio = AsyncMock(side_effect=err)
    update = make_flow_update(TRACK_URL, cap_tg)

    await download.run_spotify_download(update, context, TRACK_URL)

    assert await decorators.join_pending_db_work()
    db = SessionLocal()
    rows = cruds.recent_tracks(db, cap_tg)
    assert len(rows) == 1
    assert rows[0].title == "T1"
    assert rows[0].file_id is None
    assert rows[0].spotify_url == song.url
    db.close()


# --- handler surface ----------------------------------------------------------------

def make_lib_track(**over):
    base = dict(
        track_key="k1", title="T1", artist="Artist", album="Album",
        duration_sec=200, cover_url="http://cover/x.jpg",
        spotify_url=TRACK_URL, source="spotify",
        file_id="AgCloudFile", orig_chat_id=CHAT_ID, orig_message_id=1234,
        request_count=2, added_on=datetime.now(),
        last_requested=datetime.now() - timedelta(hours=5),
    )
    base.update(over)
    return library.LibraryTrack(**base)


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    context.bot = MagicMock()
    for m in ("send_chat_action", "send_audio", "send_photo",
              "send_message", "edit_message_text", "edit_message_media",
              "copy_message", "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    return context


def make_card_message(chat_id=CHAT_ID, message_id=600):
    msg = MagicMock(chat_id=chat_id, message_id=message_id)
    for media in ("audio", "video", "document", "animation", "voice",
                  "video_note"):
        setattr(msg, media, None)
    msg.reply_text = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def make_callback(data, tg_id=OWNER, chat_id=CHAT_ID, chat_type="private"):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    cq.message = make_card_message(chat_id=chat_id)
    update.callback_query = cq
    update.effective_message = cq.message
    update.effective_chat = MagicMock(id=chat_id, type=chat_type)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    return update


def _reply_text(update) -> str:
    call = update.effective_message.reply_text.await_args
    return call.args[0] if call.args else call.kwargs["text"]


def put_snapshot(context, tg_id=OWNER, tracks=None, page=1, pages=1):
    entry = {
        "tg_id": tg_id, "page": page, "pages": pages,
        "total": len(tracks or []),
        "tracks": tracks or [make_lib_track()],
    }
    return get_token_store(context, lib_handler.LIB_STORE).put(entry, 300)


async def test_open_library_empty(monkeypatch):
    async def fake_read(tg_id, page=1, page_size=10):
        return [], 0
    monkeypatch.setattr(library, "read_library", fake_read)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_NONE
    cbs = [b.callback_data
           for row in edited["reply_markup"].inline_keyboard for b in row]
    assert "nav:search" in cbs and "nav:home" in cbs


async def test_open_library_renders_tokenized_card(monkeypatch):
    async def fake_read(tg_id, page=1, page_size=10):
        return [make_lib_track()], 1
    monkeypatch.setattr(library, "read_library", fake_read)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_media.await_args.kwargs
    assert edited["media"].media == "http://cover/x.jpg"
    assert "T1" in edited["media"].caption
    markup = edited["reply_markup"]
    store = get_token_store(context, lib_handler.LIB_STORE)
    assert len(store) == 1
    token = next(iter(store._data))
    cbs = [b.callback_data for row in markup.inline_keyboard
           for b in row]
    assert f"lib:dl:{token}:1" in cbs
    assert f"lib:prev:{token}:1" in cbs
    assert f"lib:rm:{token}:1" in cbs
    assert f"lib:prevcard:{token}:1" not in cbs  # single track: no carousel
    assert "nav:tasks" in cbs and "nav:home" in cbs


async def test_open_library_unavailable(monkeypatch):
    async def fake_read(tg_id, page=1, page_size=10):
        return None
    monkeypatch.setattr(library, "read_library", fake_read)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_UNAVAILABLE
    cbs = [b.callback_data
           for row in edited["reply_markup"].inline_keyboard for b in row]
    assert "nav:library" in cbs  # Try again re-opens the view


async def test_open_library_group_toasts_private():
    """Nothing library-related is ever rendered into a group (plan §3)."""
    update = make_callback("nav:library", chat_id=-100,
                           chat_type="supergroup")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_PRIVATE)
    context.bot.send_message.assert_not_awaited()
    context.bot.edit_message_text.assert_not_awaited()


async def test_lib_action_stale_token():
    update = make_callback("lib:dl:deadbeef:1")
    context = make_context()

    await lib_handler.on_lib_action(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()
    context.bot.copy_message.assert_not_awaited()


async def test_lib_action_foreign_owner_toasts():
    """The library is private by construction: a foreign tap on the
    card gets the stale toast and runs nothing."""
    context = make_context()
    token = put_snapshot(context, tg_id=FOREIGN)
    update = make_callback(f"lib:dl:{token}:1")  # tapper is OWNER

    await lib_handler.on_lib_action(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_lib_dl_cloud_resend(monkeypatch):
    """Ladder step 1: sendAudio by file_id — server-side relay, no
    thumbnail (can't be re-sent), protect_content off."""
    context = make_context()
    token = put_snapshot(context)
    requests = []
    monkeypatch.setattr(
        library, "record_request",
        lambda tg_id, key: requests.append((tg_id, key)),
    )
    update = make_callback(f"lib:dl:{token}:1")

    await lib_handler.on_lib_action(update, context)

    sa = context.bot.send_audio.await_args.kwargs
    assert sa["audio"] == "AgCloudFile"
    assert sa["title"] == "T1"
    assert sa["performer"] == "Artist"
    assert sa["duration"] == 200
    assert sa["protect_content"] is False
    assert "thumbnail" not in sa
    context.bot.copy_message.assert_not_awaited()
    assert requests == [(OWNER, "k1")]


async def test_lib_dl_falls_back_to_copy_message(monkeypatch):
    """Ladder step 2: the stored file_id is gone — copy the original
    delivery (refs known) instead."""
    context = make_context()
    context.bot.send_audio = AsyncMock(
        side_effect=BadRequest("file is not found"))
    monkeypatch.setattr(library, "record_request", lambda *a: None)
    token = put_snapshot(context)
    update = make_callback(f"lib:dl:{token}:1")

    await lib_handler.on_lib_action(update, context)

    cp = context.bot.copy_message.await_args.kwargs
    assert cp["from_chat_id"] == CHAT_ID
    assert cp["message_id"] == 1234
    assert cp["protect_content"] is False


async def test_lib_dl_falls_back_to_redownload(monkeypatch):
    """Ladder step 3: no cloud handles at all — the existing download
    flow runs (and its delivery capture refreshes the row)."""
    context = make_context()
    track = make_lib_track(file_id=None, orig_chat_id=None,
                           orig_message_id=None)
    token = put_snapshot(context, tracks=[track])
    redownloads = []

    async def fake_redownload(update, context, url):
        redownloads.append(url)
    monkeypatch.setattr(download, "run_spotify_download",
                        fake_redownload)
    update = make_callback(f"lib:dl:{token}:1")

    await lib_handler.on_lib_action(update, context)

    context.bot.send_audio.assert_not_awaited()
    context.bot.copy_message.assert_not_awaited()
    assert redownloads == [TRACK_URL]


async def test_lib_dl_ladder_exhausted_says_no_resend():
    """No file_id, no original message, no link: the honest class-2
    answer, never a silent skip."""
    context = make_context()
    track = make_lib_track(file_id=None, orig_chat_id=None,
                           orig_message_id=None, spotify_url=None)
    token = put_snapshot(context, tracks=[track])
    update = make_callback(f"lib:dl:{token}:1")

    await lib_handler.on_lib_action(update, context)

    assert _reply_text(update) == copy.LIB_NO_RESEND


async def test_lib_dl_flood_control_waits():
    """429 on the re-send: tell the user the wait, the tap is still
    there."""
    context = make_context()
    context.bot.send_audio = AsyncMock(side_effect=RetryAfter(30))
    token = put_snapshot(context)
    update = make_callback(f"lib:dl:{token}:1")

    await lib_handler.on_lib_action(update, context)

    assert "30" in _reply_text(update)


async def test_lib_preview_uses_lookup(monkeypatch):
    context = make_context()

    async def fake_resolve(title, artist):
        return "http://preview/x.mp3"
    monkeypatch.setattr(lib_handler, "resolve_preview_url", fake_resolve)
    token = put_snapshot(context)
    update = make_callback(f"lib:prev:{token}:1")

    await lib_handler.on_lib_action(update, context)

    sa = context.bot.send_audio.await_args.kwargs
    assert sa["audio"] == "http://preview/x.mp3"
    assert sa["title"] == "T1"


async def test_lib_preview_missing_toasts(monkeypatch):
    context = make_context()

    async def fake_resolve(title, artist):
        return None
    monkeypatch.setattr(lib_handler, "resolve_preview_url", fake_resolve)
    token = put_snapshot(context)
    update = make_callback(f"lib:prev:{token}:1")

    await lib_handler.on_lib_action(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_TRACK_NO_PREVIEW)
    context.bot.send_audio.assert_not_awaited()


async def test_lib_rm_removes_and_rerenders(monkeypatch):
    context = make_context()
    removed = []

    async def fake_remove(tg_id, track_key):
        removed.append((tg_id, track_key))
        return True
    monkeypatch.setattr(library, "remove_track", fake_remove)

    async def fake_read(tg_id, page=1, page_size=10):
        return [], 0
    monkeypatch.setattr(library, "read_library", fake_read)
    token = put_snapshot(context)
    update = make_callback(f"lib:rm:{token}:1")

    await lib_handler.on_lib_action(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_REMOVED)
    assert removed == [(OWNER, "k1")]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_NONE


async def test_lib_rm_db_failure_toasts(monkeypatch):
    context = make_context()

    async def fake_remove(tg_id, track_key):
        return False
    monkeypatch.setattr(library, "remove_track", fake_remove)
    token = put_snapshot(context)
    update = make_callback(f"lib:rm:{token}:1")

    await lib_handler.on_lib_action(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_FAILED)


async def test_lib_carousel_boundaries_toast():
    context = make_context()
    tracks = [make_lib_track(track_key=f"k{i}") for i in range(1, 4)]
    token = put_snapshot(context, tracks=tracks)

    update = make_callback(f"lib:prevcard:{token}:1")
    await lib_handler.on_lib_action(update, context)
    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_FIRST)

    update = make_callback(f"lib:next:{token}:3")
    await lib_handler.on_lib_action(update, context)
    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_LAST)
    context.bot.edit_message_text.assert_not_awaited()
    context.bot.edit_message_media.assert_not_awaited()


async def test_lib_carousel_next_edits_card_in_place():
    context = make_context()
    tracks = [
        make_lib_track(track_key="k1", title="A", cover_url=None),
        make_lib_track(track_key="k2", title="B", cover_url=None),
    ]
    token = put_snapshot(context, tracks=tracks)
    update = make_callback(f"lib:next:{token}:1")

    await lib_handler.on_lib_action(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert "B" in edited["text"]
    assert "Track 2 of 2" in edited["text"]
    cbs = [b.callback_data
           for row in edited["reply_markup"].inline_keyboard for b in row]
    assert f"lib:dl:{token}:2" in cbs


async def test_lib_page_out_of_range_toasts(monkeypatch):
    async def fake_read(tg_id, page=1, page_size=10):
        return [make_lib_track()], 1
    monkeypatch.setattr(library, "read_library", fake_read)
    update = make_callback("lib:page:5")
    context = make_context()

    await lib_handler.on_lib_page(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_PAGE)
    context.bot.edit_message_text.assert_not_awaited()


# --- keyboards / copy ---------------------------------------------------------------

def test_library_keyboard_layout():
    markup = keyboards.library_keyboard("ab12cd34", index=2, total=5,
                                        page=2, pages=3)
    cbs = [b.callback_data for row in markup.inline_keyboard
           for b in row if b.callback_data]
    assert "lib:dl:ab12cd34:2" in cbs
    assert "lib:prev:ab12cd34:2" in cbs
    assert "lib:rm:ab12cd34:2" in cbs
    assert "lib:prevcard:ab12cd34:2" in cbs
    assert "lib:next:ab12cd34:2" in cbs
    assert "lib:page:1" in cbs
    assert "lib:page:3" in cbs
    assert "nav:tasks" in cbs and "nav:home" in cbs
    for cb in cbs:
        assert len(cb.encode("utf-8")) <= 64, cb

    # single page + single track: no page row, no carousel row
    markup = keyboards.library_keyboard("ab12cd34", index=1, total=1)
    cbs = [b.callback_data for row in markup.inline_keyboard
           for b in row if b.callback_data]
    assert not any(cb.startswith("lib:page:") for cb in cbs)
    assert not any(cb.startswith("lib:prevcard:") for cb in cbs)
    assert not any(cb.startswith("lib:next:") for cb in cbs)


def test_home_keyboard_carries_library_entry():
    cbs = [b.callback_data for row in keyboards.home_keyboard.inline_keyboard
           for b in row if b.callback_data]
    assert "nav:library" in cbs


def test_help_mentions_library():
    assert "My music" in copy.HELP_BODY


class _FakeSearchClient:
    def search(self, term):
        return {"tracks": {"items": [{
            "id": "t-123",
            "name": "T",
            "duration_ms": 200000,
            "artists": [{"name": "A"}],
            "album": {"name": "B", "images": [
                {"url": "http://c/x.jpg", "width": 64, "height": 64},
            ]},
            "external_urls": {"spotify":
                              "https://open.spotify.com/track/t-123"},
            "preview_url": None,
        }]}}


def test_track_result_carries_spotify_track_id(monkeypatch):
    """The search path exposes the Spotify track id — the library's
    stable dedup key (plan §4)."""
    monkeypatch.setattr(spotdl_spotify, "SpotifyClient", _FakeSearchClient)
    results = spotify_search._raw_search("T A")
    assert results[0].track_id == "t-123"
