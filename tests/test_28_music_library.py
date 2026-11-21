"""#28 Music library (docs/music-library-plan.md, UX v3 plan §12).

- schema/CRUD live against the local Postgres (unique tg_ids, cleaned
  up in teardown — same contract as test_09): upsert dedup on
  (tg_id, track_key), file-ref preservation, ordering, cascades
- delivery capture: every successful Spotify delivery upserts a
  library row carrying the Telegram file_id (the cloud re-send
  handle); the in-flight timeout case records metadata only (plan §3
  known gap)
- utils.library facade: breaker-aware background writes, thread-based
  reads that degrade to None, source/sort/search page reads
  (Python-side Unicode matching + ordering, plan §12.5), relative-time
  + caption helpers
- the chat surface (UX v3): M01 menu (4 destinations, empty/unavailable
  states), the shared M02 list with numbered [▶ n] [⋯ n] rows and
  stateless browse paging, the tokenized M05 detail + M05b remove
  confirm (fresh-row ladder, origin restore), M03/M06 library search
  (conversation state, frozen result set, cancel + URL handoff), M04
  sort selection, M07 often-empty, and the legacy v2 stale toasts
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
from utils.constants.states import HOME_STATE, LIBRARY_SEARCH_STATE
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


async def test_library_page_returns_none_on_failure(test_tg, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")
    monkeypatch.setattr(library, "library_page_sync", boom)
    assert await library.library_page(test_tg) is None
    assert decorators._db_breaker._consecutive_failures >= 1


async def test_library_page_sources_and_paging(test_tg):
    db = SessionLocal()
    for i in range(7):
        cruds.upsert_track(db, tg_id=test_tg, track_key=f"k{i:02d}",
                           title=f"T{i}")
    db.close()

    tracks, total = await library.library_page(test_tg, "latest", page=1)
    assert total == 7 and len(tracks) == 5
    tracks, total = await library.library_page(test_tg, "latest", page=2)
    assert len(tracks) == 2
    # a stale page (rows removed since render) falls back to page 1
    tracks, total = await library.library_page(test_tg, "latest", page=99)
    assert len(tracks) == 5
    first = {t.track_key for t in
             (await library.library_page(test_tg, "latest", page=1))[0]}
    assert {t.track_key for t in tracks} == first


async def test_browse_sorts(test_tg):
    db = SessionLocal()
    for i, title in enumerate(["banana", "Apple", "cherry"]):
        cruds.upsert_track(db, tg_id=test_tg, track_key=f"k{i}", title=title)
    db.close()

    tracks, total = await library.library_page(test_tg, "browse", sort="az")
    assert total == 3
    assert [t.title for t in tracks] == ["Apple", "banana", "cherry"]
    tracks, total = await library.library_page(test_tg, "browse",
                                               sort="recent")
    assert total == 3 and len(tracks) == 3


async def test_often_requires_two_requests(test_tg):
    db = SessionLocal()
    cruds.upsert_track(db, tg_id=test_tg, track_key="once", title="One")
    cruds.upsert_track(db, tg_id=test_tg, track_key="twice", title="Two")
    cruds.upsert_track(db, tg_id=test_tg, track_key="twice", title="Two")
    db.close()

    tracks, total = await library.library_page(test_tg, "often")
    assert total == 1
    assert tracks[0].track_key == "twice"
    assert tracks[0].request_count == 2


async def test_search_library_matching_and_relevance(test_tg):
    db = SessionLocal()
    for key, title, artist in [
        ("b1", "Because", "Kazz Hawkins"),
        ("b2", "Baby", "Kazz Hawkins"),
        ("b3", "Yesterday", "Beatles"),
        ("b4", "Imagine", "Lennon"),
    ]:
        cruds.upsert_track(db, tg_id=test_tg, track_key=key,
                           title=title, artist=artist)
    db.close()

    # casefold matching (upper-case query)
    assert [t.track_key for t in
            await library.search_library(test_tg, "YESTERDAY")] == ["b3"]
    # artist match: both "hawk" tracks, tie broken by newest request
    assert [t.track_key for t in
            await library.search_library(test_tg, "hawk")] == ["b2", "b1"]
    # combined title+artist match (neither field alone contains it)
    assert [t.track_key for t in await library.search_library(
        test_tg, "yesterday beatles")] == ["b3"]
    # below the 2-char minimum / no match
    assert await library.search_library(test_tg, "y") == []
    assert await library.search_library(test_tg, "zzz") == []


async def test_search_library_returns_none_on_failure(test_tg, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")
    monkeypatch.setattr(library, "search_library_sync", boom)
    assert await library.search_library(test_tg, "because") is None


async def test_get_library_track_fresh(test_tg):
    db = SessionLocal()
    cruds.upsert_track(db, tg_id=test_tg, track_key="k1", title="T",
                       file_ref=("AgF", 7, 9))
    db.close()
    track = await library.get_library_track(test_tg, "k1")
    assert track is not None and track.file_id == "AgF"
    assert await library.get_library_track(test_tg, "nope") is None


async def test_remove_track_async(test_tg):
    db = SessionLocal()
    cruds.upsert_track(db, tg_id=test_tg, track_key="k1", title="T")
    db.close()
    assert await library.remove_track(test_tg, "k1") is True
    assert await library.remove_track(test_tg, "k1") is False


def test_relative_time_duration_and_captions():
    now = datetime.now()
    assert library.relative_time(now) == "just now"
    assert library.relative_time(now - timedelta(hours=5)) == "5 h ago"
    assert library.relative_time(now - timedelta(days=2)) == "2 d ago"
    assert library.relative_time(None) == "—"

    assert library.duration_fmt(236) == "3:56"
    assert library.duration_fmt(65) == "1:05"
    assert library.duration_fmt(None) == ""

    track = SimpleNamespace(
        title="T<b>", artist="A", album="B", duration_sec=236,
        request_count=3, last_requested=now,
    )
    caption = library.library_list_caption("Latest", 2, 1, 2, [track])
    assert "📚 <b>Your music</b> · Latest" in caption
    assert "2 tracks · Page 1 of 2" in caption
    assert "1. <b>T&lt;b&gt;</b>" in caption  # escaped
    assert "<i>A</i> · 3:56" in caption
    assert "Requested 3 times · Last just now\n" in caption

    # a single page shows no page line (design §12.3 edge)
    caption = library.library_list_caption("Latest", 1, 1, 1, [track])
    assert "1 track" in caption and "Page" not in caption

    # detail screen: missing fields omit their line
    bare = SimpleNamespace(
        title="T", artist="", album="", duration_sec=None,
        request_count=1, last_requested=now, added_on=now,
    )
    detail = library.library_detail_caption(bare)
    assert "🎵 Track details" in detail
    assert "<b>T</b>" in detail
    assert "Artist:" not in detail and "Album:" not in detail
    assert "Duration:" not in detail
    assert "Requested: 1 times" in detail
    assert copy.LIB_DETAIL_PROMPT in detail

    # search results: count is the TOTAL (not the page), query escaped
    results = library.library_search_results_caption("a<b>", 2, 3, 12,
                                                     [track])
    assert "🔎 Search results: <i>a&lt;b&gt;</i>" in results
    assert "Found 12 tracks · Page 2 of 3" in results
    assert 'No tracks matched "q".' in \
        library.library_search_none_caption("q")


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


def _sent_text(update) -> str:
    call = update.effective_message.reply_text.await_args
    return call.args[0] if call.args else call.kwargs["text"]


def make_text_update(text_, tg_id=OWNER, chat_id=CHAT_ID,
                     chat_type="private"):
    update = MagicMock()
    msg = MagicMock(chat_id=chat_id, message_id=700, text=text_,
                    reply_text=AsyncMock())
    update.effective_message = msg
    update.callback_query = None
    update.effective_chat = MagicMock(id=chat_id, type=chat_type)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    return update


def _cbs(markup) -> list:
    return [b.callback_data for row in markup.inline_keyboard
            for b in row if b.callback_data]


def put_list_snapshot(context, tg_id=OWNER, tracks=None, page=1, pages=1,
                      source="latest", sort=None, query=None):
    tracks = tracks or [make_lib_track()]
    entry = {"source": source, "tg_id": tg_id, "sort": sort,
             "query": query, "page": page, "pages": pages,
             "total": len(tracks), "tracks": tracks}
    return get_token_store(context, lib_handler.LIB_STORE).put(entry, 300)


def put_detail_snapshot(context, tg_id=OWNER, track=None, origin=None):
    track = track or make_lib_track()
    origin = origin or {"source": "latest", "sort": None,
                        "query": None, "page": 1}
    entry = {"source": "detail", "tg_id": tg_id,
             "track_key": track.track_key, "track": track,
             "origin": origin}
    return get_token_store(context, lib_handler.LIB_STORE).put(entry, 300)


def _patch_page(monkeypatch, pages, total):
    """A fake `library.library_page` serving fixed pages (`pages` is a
    list of track lists, page 1 first; `pages=None` simulates the DB
    being down). Returns a read log of (source, sort, page) calls."""
    read = {"pages": []}

    async def fake_page(tg_id, source="latest", sort=None, query=None,
                        page=1, page_size=library.LIB_PAGE_SIZE):
        read["pages"].append((source, sort, page))
        if pages is None:
            return None
        if total == 0:
            return [], 0
        if page > len(pages):
            page = 1  # stale-page fallback, like the real read
        return pages[page - 1], total

    monkeypatch.setattr(library, "library_page", fake_page)
    return read


async def test_menu_renders_four_destinations(monkeypatch):
    """M01 — the four Phase A destinations in fixed order; the menu
    itself stores no snapshot."""
    _patch_page(monkeypatch, [[make_lib_track()]], 64)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == lib_handler._menu_text(64)
    assert _cbs(edited["reply_markup"]) == [
        "lib:latest", "lib:search", "lib:often", "lib:browse",
        "nav:tasks", "nav:home",
    ]
    assert len(get_token_store(context, lib_handler.LIB_STORE)) == 0


async def test_menu_singular_word(monkeypatch):
    _patch_page(monkeypatch, [[make_lib_track()]], 1)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert "1 track saved" in edited["text"]


async def test_menu_empty_library_points_at_search(monkeypatch):
    _patch_page(monkeypatch, [], 0)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_NONE
    assert _cbs(edited["reply_markup"]) == ["nav:search", "nav:home"]


async def test_menu_unavailable(monkeypatch):
    _patch_page(monkeypatch, None, 0)
    update = make_callback("nav:library")
    context = make_context()

    await lib_handler.on_library_callback(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_UNAVAILABLE
    # Try again re-opens the view; Tasks is not on this card (design)
    assert _cbs(edited["reply_markup"]) == ["nav:library", "nav:home"]


async def test_latest_renders_numbered_rows(monkeypatch):
    """M02 — one [▶ n] [⋯ n] row per track with full metadata, page nav,
    Menu, escape; the render stores a frozen page snapshot."""
    read = _patch_page(
        monkeypatch,
        [[make_lib_track(track_key=f"k{i}", title=f"T{i}")
          for i in range(1, 6)]], 12)
    update = make_callback("lib:latest")
    context = make_context()

    await lib_handler.on_lib_destination(update, context)

    assert read["pages"] == [("latest", None, 1)]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"].startswith("📚 <b>Your music</b> · Latest")
    assert "12 tracks · Page 1 of 3" in edited["text"]
    for i in range(1, 6):
        assert f"{i}. <b>T{i}</b>" in edited["text"]
    assert "   <i>Artist</i> · 3:20" in edited["text"]
    assert "Requested 2 times · Last 5 h ago\n" in edited["text"]
    assert copy.LIB_LIST_PROMPT in edited["text"]
    token = next(iter(get_token_store(context, lib_handler.LIB_STORE)._data))
    cbs = _cbs(edited["reply_markup"])
    for i in range(1, 6):
        assert f"lib:get:{token}:{i}" in cbs
        assert f"lib:more:{token}:{i}" in cbs
    assert "lib:page:latest:2" in cbs
    assert "nav:library" in cbs and "nav:tasks" in cbs and "nav:home" in cbs
    assert len(edited["reply_markup"].inline_keyboard) == 8  # row budget


async def test_latest_single_page_hides_pagination(monkeypatch):
    _patch_page(monkeypatch, [[make_lib_track()]], 3)
    update = make_callback("lib:latest")
    context = make_context()

    await lib_handler.on_lib_destination(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert "3 tracks" in edited["text"]
    assert "Page" not in edited["text"]  # single page: no page line/row
    cbs = _cbs(edited["reply_markup"])
    assert not any(cb.startswith("lib:page:") for cb in cbs)
    assert len(edited["reply_markup"].inline_keyboard) == 3


async def test_requested_once_wording(monkeypatch):
    _patch_page(monkeypatch, [[make_lib_track(request_count=1)]], 1)
    update = make_callback("lib:latest")
    context = make_context()

    await lib_handler.on_lib_destination(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert "Requested once" in edited["text"]


async def test_page_callback_renders_requested_page(monkeypatch):
    all_tracks = [make_lib_track(track_key=f"k{i}", title=f"T{i}")
                  for i in range(1, 13)]
    read = _patch_page(monkeypatch,
                       [all_tracks[0:5], all_tracks[5:10],
                        all_tracks[10:13]], 12)
    update = make_callback("lib:page:latest:2")
    context = make_context()

    await lib_handler.on_lib_page(update, context)

    assert ("latest", None, 1) in read["pages"]  # total probe
    assert ("latest", None, 2) in read["pages"]  # render
    edited = context.bot.edit_message_text.await_args.kwargs
    assert "Page 2 of 3" in edited["text"]
    # browse-family numbering restarts each page: the snapshot IS the
    # page, and the button number is the row's position in it
    assert "1. <b>T6</b>" in edited["text"]
    assert "5. <b>T10</b>" in edited["text"]
    assert "6." not in edited["text"]
    cbs = _cbs(edited["reply_markup"])
    assert "lib:page:latest:1" in cbs  # Previous
    assert "lib:page:latest:3" in cbs  # Next


async def test_page_out_of_range_toasts(monkeypatch):
    _patch_page(monkeypatch, [[make_lib_track()] * 5], 5)
    update = make_callback("lib:page:latest:2")
    context = make_context()

    await lib_handler.on_lib_page(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_PAGE)
    context.bot.edit_message_text.assert_not_awaited()


async def test_page_db_failure_toasts(monkeypatch):
    _patch_page(monkeypatch, None, 0)
    update = make_callback("lib:page:latest:2")
    context = make_context()

    await lib_handler.on_lib_page(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_FAILED)


async def test_row_more_opens_detail(monkeypatch):
    """M05 — ⋯ n opens the detail: a fresh DB read (not the snapshot
    copy) and a detail snapshot carrying the origin list."""
    context = make_context()
    put_list_snapshot(context, tracks=[make_lib_track(),
                                       make_lib_track(track_key="k2")])
    store = get_token_store(context, lib_handler.LIB_STORE)
    token = next(iter(store._data))
    fresh = make_lib_track()
    fresh_reads = []

    async def fake_fresh(tg_id, track_key):
        fresh_reads.append((tg_id, track_key))
        return fresh
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:more:{token}:1")

    await lib_handler.on_lib_row(update, context)

    assert fresh_reads == [(OWNER, "k1")]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == library.library_detail_caption(fresh)
    cbs = _cbs(edited["reply_markup"])
    detail_tokens = [t for t in list(store._data)
                     if (store.get(t) or {}).get("source") == "detail"]
    assert len(detail_tokens) == 1
    detail_token = detail_tokens[0]
    for action in ("dl", "pv", "rm", "back"):
        assert f"lib:{action}:{detail_token}" in cbs
    # the Open-on-Spotify row is a URL button — no payload, survives restarts
    url_buttons = [b for row in edited["reply_markup"].inline_keyboard
                   for b in row if b.url]
    assert [b.url for b in url_buttons] == [TRACK_URL]
    assert "nav:library" in cbs
    entry = store.get(detail_token)
    assert entry["origin"]["source"] == "latest"
    assert entry["origin"]["page"] == 1


async def test_row_more_deleted_track_toasts_not_found(monkeypatch):
    context = make_context()
    token = put_list_snapshot(context)

    async def fake_fresh(tg_id, track_key):
        return None
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:more:{token}:1")

    await lib_handler.on_lib_row(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_NOT_FOUND)
    context.bot.edit_message_text.assert_not_awaited()


async def test_row_get_redelivers_with_source_list_keyboard(monkeypatch):
    """▶ i re-sends (ladder step 1); the delivered audio carries the
    SOURCE LIST's keyboard — actions keep working from the message."""
    context = make_context()
    token = put_list_snapshot(context)
    requests = []
    monkeypatch.setattr(
        library, "record_request",
        lambda tg_id, key: requests.append((tg_id, key)),
    )

    async def fake_fresh(tg_id, track_key):
        return make_lib_track()
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:get:{token}:1")

    await lib_handler.on_lib_row(update, context)

    sa = context.bot.send_audio.await_args.kwargs
    assert sa["audio"] == "AgCloudFile"
    assert sa["title"] == "T1"
    assert sa["performer"] == "Artist"
    assert sa["duration"] == 200
    assert sa["protect_content"] is False
    assert "thumbnail" not in sa
    assert f"lib:get:{token}:1" in _cbs(sa["reply_markup"])
    context.bot.copy_message.assert_not_awaited()
    assert requests == [(OWNER, "k1")]


async def test_row_get_falls_back_to_copy_message(monkeypatch):
    """Ladder step 2: the stored file_id is gone — copy the original
    delivery (refs known) instead."""
    context = make_context()
    context.bot.send_audio = AsyncMock(
        side_effect=BadRequest("file is not found"))
    monkeypatch.setattr(library, "record_request", lambda *a: None)
    token = put_list_snapshot(context)

    async def fake_fresh(tg_id, track_key):
        return make_lib_track()
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:get:{token}:1")

    await lib_handler.on_lib_row(update, context)

    cp = context.bot.copy_message.await_args.kwargs
    assert cp["from_chat_id"] == CHAT_ID
    assert cp["message_id"] == 1234
    assert cp["protect_content"] is False


async def test_row_get_falls_back_to_redownload(monkeypatch):
    """Ladder step 3: no cloud handles at all — the existing download
    flow runs (and its delivery capture refreshes the row)."""
    context = make_context()
    track = make_lib_track(file_id=None, orig_chat_id=None,
                           orig_message_id=None)
    token = put_list_snapshot(context, tracks=[track])
    redownloads = []

    async def fake_redownload(update, context, url):
        redownloads.append(url)
    monkeypatch.setattr(download, "run_spotify_download",
                        fake_redownload)

    async def fake_fresh(tg_id, track_key):
        return track
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:get:{token}:1")

    await lib_handler.on_lib_row(update, context)

    context.bot.send_audio.assert_not_awaited()
    context.bot.copy_message.assert_not_awaited()
    assert redownloads == [TRACK_URL]


async def test_row_get_ladder_exhausted_says_no_resend(monkeypatch):
    """No file_id, no original message, no link: the honest class-2
    answer, never a silent skip."""
    context = make_context()
    track = make_lib_track(file_id=None, orig_chat_id=None,
                           orig_message_id=None, spotify_url=None)
    token = put_list_snapshot(context, tracks=[track])

    async def fake_fresh(tg_id, track_key):
        return track
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:get:{token}:1")

    await lib_handler.on_lib_row(update, context)

    sent = context.bot.send_message.await_args.kwargs
    assert sent["text"] == copy.LIB_NO_RESEND
    assert _cbs(sent["reply_markup"]) == [
        "nav:search", "nav:library", "nav:home"]


async def test_row_get_flood_control_waits(monkeypatch):
    """429 on the re-send: a visible wait card; its Try-again is the
    ORIGINAL payload (an early re-tap just gets an honest 429)."""
    context = make_context()
    context.bot.send_audio = AsyncMock(side_effect=RetryAfter(30))
    token = put_list_snapshot(context)

    async def fake_fresh(tg_id, track_key):
        return make_lib_track()
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:get:{token}:1")

    await lib_handler.on_lib_row(update, context)

    sent = context.bot.send_message.await_args.kwargs
    assert "30" in sent["text"]
    assert _cbs(sent["reply_markup"]) == [f"lib:get:{token}:1",
                                          "nav:tasks", "nav:home"]


async def test_row_out_of_range_toasts():
    context = make_context()
    token = put_list_snapshot(context, tracks=[make_lib_track()])
    update = make_callback(f"lib:get:{token}:7")

    await lib_handler.on_lib_row(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_row_action_on_detail_token_toasts():
    """Row actions resolve against list entries only — a detail token
    is stale, not a different row."""
    context = make_context()
    token = put_detail_snapshot(context)
    update = make_callback(f"lib:get:{token}:1")

    await lib_handler.on_lib_row(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_row_stale_token_toasts():
    context = make_context()
    update = make_callback("lib:get:deadbeef:1")

    await lib_handler.on_lib_row(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()
    context.bot.copy_message.assert_not_awaited()


async def test_row_foreign_owner_toasts():
    """The library is private by construction: a foreign tap on the
    card gets the stale toast and runs nothing."""
    context = make_context()
    token = put_list_snapshot(context, tg_id=FOREIGN)
    update = make_callback(f"lib:get:{token}:1")  # tapper is OWNER

    await lib_handler.on_lib_row(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_detail_dl_redelivers_with_fresh_origin_keyboard(monkeypatch):
    """Get again from M05: the audio carries the ORIGIN list's keyboard
    — a fresh read + fresh token, so ▶/⋯ keep working."""
    context = make_context()
    token = put_detail_snapshot(context)
    requests = []
    monkeypatch.setattr(
        library, "record_request",
        lambda tg_id, key: requests.append((tg_id, key)),
    )
    _patch_page(monkeypatch,
                [[make_lib_track(track_key=f"k{i}") for i in range(1, 6)]],
                5)

    async def fake_fresh(tg_id, track_key):
        return make_lib_track()
    monkeypatch.setattr(library, "get_library_track", fake_fresh)
    update = make_callback(f"lib:dl:{token}")

    await lib_handler.on_lib_detail(update, context)

    sa = context.bot.send_audio.await_args.kwargs
    assert sa["audio"] == "AgCloudFile"
    assert any(cb.startswith("lib:get:") for cb in _cbs(sa["reply_markup"]))
    assert requests == [(OWNER, "k1")]


async def test_detail_pv_preview(monkeypatch):
    context = make_context()
    token = put_detail_snapshot(context)

    async def fake_fresh(tg_id, track_key):
        return make_lib_track()
    monkeypatch.setattr(library, "get_library_track", fake_fresh)

    async def fake_resolve(title, artist):
        return "http://preview/x.mp3"
    monkeypatch.setattr(lib_handler, "resolve_preview_url", fake_resolve)
    update = make_callback(f"lib:pv:{token}")

    await lib_handler.on_lib_detail(update, context)

    sa = context.bot.send_audio.await_args.kwargs
    assert sa["audio"] == "http://preview/x.mp3"
    assert sa["title"] == "T1"


async def test_detail_pv_missing_toasts(monkeypatch):
    context = make_context()
    token = put_detail_snapshot(context)

    async def fake_fresh(tg_id, track_key):
        return make_lib_track()
    monkeypatch.setattr(library, "get_library_track", fake_fresh)

    async def fake_resolve(title, artist):
        return None
    monkeypatch.setattr(lib_handler, "resolve_preview_url", fake_resolve)
    update = make_callback(f"lib:pv:{token}")

    await lib_handler.on_lib_detail(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_TRACK_NO_PREVIEW)
    context.bot.send_audio.assert_not_awaited()


async def test_detail_rm_shows_confirm(monkeypatch):
    """M05b — Remove never deletes on the first tap; the confirm
    screen carries both choices."""
    context = make_context()
    token = put_detail_snapshot(context)
    update = make_callback(f"lib:rm:{token}")

    await lib_handler.on_lib_detail(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_REMOVE_CONFIRM.format(
        title="T1", artist="Artist")
    cbs = _cbs(edited["reply_markup"])
    assert f"lib:rmc:{token}" in cbs
    assert f"lib:keep:{token}" in cbs
    assert "nav:library" in cbs


async def test_detail_rmc_removes_and_restores_origin(monkeypatch):
    context = make_context()
    token = put_detail_snapshot(context)
    removed = []

    async def fake_remove(tg_id, track_key):
        removed.append((tg_id, track_key))
        return True
    monkeypatch.setattr(library, "remove_track", fake_remove)
    _patch_page(monkeypatch, [], 0)  # the origin list is now empty
    update = make_callback(f"lib:rmc:{token}")

    await lib_handler.on_lib_detail(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_REMOVED)
    assert removed == [(OWNER, "k1")]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_NONE


async def test_detail_rmc_db_failure_toasts(monkeypatch):
    context = make_context()
    token = put_detail_snapshot(context)

    async def fake_remove(tg_id, track_key):
        return False
    monkeypatch.setattr(library, "remove_track", fake_remove)
    update = make_callback(f"lib:rmc:{token}")

    await lib_handler.on_lib_detail(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_FAILED)
    context.bot.edit_message_text.assert_not_awaited()


async def test_detail_keep_rerenders_detail():
    context = make_context()
    token = put_detail_snapshot(context)
    update = make_callback(f"lib:keep:{token}")

    await lib_handler.on_lib_detail(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == library.library_detail_caption(
        make_lib_track())


async def test_detail_back_restores_origin_list(monkeypatch):
    context = make_context()
    origin = {"source": "browse", "sort": "az", "query": None, "page": 2}
    token = put_detail_snapshot(context, origin=origin)
    read = _patch_page(
        monkeypatch,
        [[make_lib_track(track_key=f"k{i}") for i in range(6, 11)]], 12)
    update = make_callback(f"lib:back:{token}")

    await lib_handler.on_lib_detail(update, context)

    assert ("browse", "az", 2) in read["pages"]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert "Title A–Z" in edited["text"]
    assert "Page 2 of 3" in edited["text"]


async def test_detail_stale_token_toasts():
    context = make_context()
    update = make_callback("lib:dl:deadbeef")

    await lib_handler.on_lib_detail(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_detail_foreign_owner_toasts():
    context = make_context()
    token = put_detail_snapshot(context, tg_id=FOREIGN)
    update = make_callback(f"lib:dl:{token}")

    await lib_handler.on_lib_detail(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_search_start_renders_prompt_and_enters_state():
    update = make_callback("lib:search")
    context = make_context()

    state = await lib_handler.on_lib_search_start(update, context)

    assert state == LIBRARY_SEARCH_STATE
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"].startswith(copy.LIB_SEARCH_TITLE)
    assert copy.LIB_SEARCH_PROMPT in edited["text"]
    assert copy.LIB_SEARCH_HINT in edited["text"]
    assert copy.LIB_SEARCH_CANCEL_HINT in edited["text"]
    assert _cbs(edited["reply_markup"]) == [
        "lib:scx", "nav:library", "nav:tasks", "nav:home"]
    # the prompt card's id is remembered for /cancel
    assert context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] == \
        (CHAT_ID, 600)


async def test_search_new_reenters_state():
    update = make_callback("lib:scn")
    context = make_context()

    state = await lib_handler.on_lib_search_new(update, context)

    assert state == LIBRARY_SEARCH_STATE
    assert context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] == \
        (CHAT_ID, 600)


async def test_search_cancel_callback(monkeypatch):
    _patch_page(monkeypatch, [[make_lib_track()]], 64)
    update = make_callback("lib:scx")
    context = make_context()
    context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] = (CHAT_ID, 600)

    state = await lib_handler.on_lib_search_cancel(update, context)

    assert state == HOME_STATE
    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_SEARCH_CANCELLED)
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == lib_handler._menu_text(64)
    assert lib_handler.LIB_SEARCH_CARD_KEY not in context.user_data


async def test_search_cancel_command_edits_prompt_card(monkeypatch):
    """/cancel inside the state: the prompt card becomes the menu again
    (edited in place via the stored id) + a short confirmation reply."""
    _patch_page(monkeypatch, [[make_lib_track()]], 64)
    update = make_text_update("/cancel")
    context = make_context()
    context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] = (CHAT_ID, 999)

    state = await lib_handler.on_lib_search_cancel_command(update, context)

    assert state == HOME_STATE
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["message_id"] == 999
    assert edited["text"] == lib_handler._menu_text(64)
    assert _sent_text(update) == copy.TOAST_LIB_SEARCH_CANCELLED


async def test_search_cancel_command_without_card_sends_fresh_menu(
        monkeypatch):
    _patch_page(monkeypatch, [[make_lib_track()]], 64)
    update = make_text_update("/cancel")
    context = make_context()

    state = await lib_handler.on_lib_search_cancel_command(update, context)

    assert state == HOME_STATE
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.kwargs["text"] == \
        lib_handler._menu_text(64)
    context.bot.edit_message_text.assert_not_awaited()
    assert _sent_text(update) == copy.TOAST_LIB_SEARCH_CANCELLED


async def test_search_text_too_short_stays_in_state():
    """<2 chars: re-render the prompt with the too-short line, stay in
    the state (the user is still expected to type a query)."""
    update = make_text_update("a")
    context = make_context()
    context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] = (CHAT_ID, 600)

    state = await lib_handler.on_lib_search_text(update, context)

    assert state == LIBRARY_SEARCH_STATE
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["message_id"] == 600
    assert edited["text"].startswith(copy.LIB_SEARCH_TOO_SHORT)
    assert context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] == \
        (CHAT_ID, 600)


async def test_search_text_renders_results_and_exits_state(monkeypatch):
    """M06 — results edit the prompt card in place; the snapshot freezes
    the FULL set (its pages slice from the freeze)."""
    results = [make_lib_track(track_key=f"k{i}", title=f"T{i}")
               for i in range(1, 8)]
    searches = []

    async def fake_search(tg_id, query):
        searches.append((tg_id, query))
        return results
    monkeypatch.setattr(library, "search_library", fake_search)
    update = make_text_update("because")
    context = make_context()
    context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] = (CHAT_ID, 600)

    state = await lib_handler.on_lib_search_text(update, context)

    assert state == HOME_STATE
    assert searches == [(OWNER, "because")]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["message_id"] == 600
    assert "🔎 Search results: <i>because</i>" in edited["text"]
    assert "Found 7 tracks · Page 1 of 2" in edited["text"]
    assert "6. <b>T6</b>" not in edited["text"]  # page 1 shows five
    store = get_token_store(context, lib_handler.LIB_STORE)
    token = next(iter(store._data))
    cbs = _cbs(edited["reply_markup"])
    for i in range(1, 6):
        assert f"lib:get:{token}:{i}" in cbs
    # multi-page: New search folds into the page row (row budget)
    assert ["lib:scn", f"lib:spg:{token}:2"] == [
        b.callback_data for b in edited["reply_markup"].inline_keyboard[5]]
    entry = store.get(token)
    assert entry["source"] == "search"
    assert len(entry["tracks"]) == 7  # the FULL frozen set
    assert entry["query"] == "because"
    assert lib_handler.LIB_SEARCH_CARD_KEY not in context.user_data


async def test_search_text_zero_results_card(monkeypatch):
    async def fake_search(tg_id, query):
        return []
    monkeypatch.setattr(library, "search_library", fake_search)
    update = make_text_update("zzz")
    context = make_context()
    context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] = (CHAT_ID, 600)

    state = await lib_handler.on_lib_search_text(update, context)

    assert state == HOME_STATE
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == library.library_search_none_caption("zzz")
    cbs = _cbs(edited["reply_markup"])
    assert "lib:scn" in cbs and "lib:browse" in cbs
    assert "nav:library" in cbs


async def test_search_text_db_down(monkeypatch):
    async def fake_search(tg_id, query):
        return None
    monkeypatch.setattr(library, "search_library", fake_search)
    update = make_text_update("because")
    context = make_context()

    state = await lib_handler.on_lib_search_text(update, context)

    assert state == HOME_STATE
    assert context.bot.send_message.await_args.kwargs["text"] == \
        copy.LIB_UNAVAILABLE


async def test_search_text_group_toasts_private():
    update = make_text_update("because", chat_type="supergroup")
    context = make_context()

    state = await lib_handler.on_lib_search_text(update, context)

    assert state == HOME_STATE
    assert _sent_text(update) == copy.TOAST_LIB_PRIVATE


async def test_search_page_renders_from_freeze_without_db(monkeypatch):
    """Search paging slices the frozen set — no DB read, positions
    never shift under a rendered card (design §6.6)."""
    context = make_context()
    tracks = [make_lib_track(track_key=f"k{i}", title=f"T{i}")
              for i in range(1, 8)]
    token = put_list_snapshot(context, tracks=tracks, page=1, pages=2,
                              source="search", query="because")

    def no_db(*args, **kwargs):
        raise AssertionError("search paging must not touch the DB")
    monkeypatch.setattr(library, "library_page", no_db)
    monkeypatch.setattr(library, "search_library", no_db)

    update = make_callback(f"lib:spg:{token}:2")
    await lib_handler.on_lib_search_page(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert "Found 7 tracks · Page 2 of 2" in edited["text"]
    assert "6. <b>T6</b>" in edited["text"]
    assert "7. <b>T7</b>" in edited["text"]
    # a fresh snapshot for the new page
    tokens = get_token_store(context, lib_handler.LIB_STORE)._data
    assert len(tokens) == 2
    cbs = _cbs(edited["reply_markup"])
    new_token = [t for t in tokens if t != token][0]
    assert f"lib:spg:{new_token}:1" in cbs  # Previous


async def test_search_page_out_of_range_toasts():
    context = make_context()
    tracks = [make_lib_track(track_key=f"k{i}") for i in range(1, 6)]
    token = put_list_snapshot(context, tracks=tracks, page=1, pages=1,
                              source="search", query="q")
    update = make_callback(f"lib:spg:{token}:2")

    await lib_handler.on_lib_search_page(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_PAGE)
    context.bot.edit_message_text.assert_not_awaited()


async def test_search_page_stale_token_toasts():
    context = make_context()
    update = make_callback("lib:spg:deadbeef:2")

    await lib_handler.on_lib_search_page(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)


async def test_browse_renders_sort_selection(monkeypatch):
    """M04 — the sort picker stores nothing and reads nothing."""
    read = _patch_page(monkeypatch, None, 0)
    update = make_callback("lib:browse")
    context = make_context()

    await lib_handler.on_lib_destination(update, context)

    assert read["pages"] == []
    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == "\n\n".join([copy.LIB_BROWSE_TITLE,
                                          copy.LIB_SORT_PROMPT])
    assert _cbs(edited["reply_markup"]) == [
        "lib:sort:recent", "lib:sort:added", "lib:sort:az",
        "nav:library", "nav:tasks", "nav:home"]


async def test_sort_renders_browse_list(monkeypatch):
    read = _patch_page(
        monkeypatch,
        [[make_lib_track(track_key=f"k{i}") for i in range(1, 6)]], 7)
    update = make_callback("lib:sort:az")
    context = make_context()

    await lib_handler.on_lib_sort(update, context)

    assert read["pages"] == [("browse", "az", 1)]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert "📚 <b>Your music</b> · Title A–Z" in edited["text"]
    assert "7 tracks · Page 1 of 2" in edited["text"]
    assert "lib:page:az:2" in _cbs(edited["reply_markup"])


async def test_often_renders_list(monkeypatch):
    read = _patch_page(
        monkeypatch,
        [[make_lib_track(), make_lib_track(track_key="k2")]], 2)
    update = make_callback("lib:often")
    context = make_context()

    await lib_handler.on_lib_destination(update, context)

    assert read["pages"] == [("often", None, 1)]
    edited = context.bot.edit_message_text.await_args.kwargs
    assert "📚 <b>Your music</b> · Often played" in edited["text"]
    assert "2 tracks" in edited["text"]
    assert "Page" not in edited["text"]  # single page


async def test_often_empty_card(monkeypatch):
    _patch_page(monkeypatch, [], 0)
    update = make_callback("lib:often")
    context = make_context()

    await lib_handler.on_lib_destination(update, context)

    edited = context.bot.edit_message_text.await_args.kwargs
    assert edited["text"] == copy.LIB_OFTEN_EMPTY
    assert _cbs(edited["reply_markup"]) == [
        "lib:latest", "lib:search", "nav:library", "nav:tasks", "nav:home"]


async def test_legacy_action_payload_toasts():
    """v2 payloads (card may still sit in the live chat): one toast,
    nothing runs, the spinner never hangs."""
    context = make_context()
    update = make_callback("lib:dl:12345678:1")

    await lib_handler.on_lib_legacy(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.send_audio.assert_not_awaited()


async def test_legacy_page_payload_toasts():
    context = make_context()
    update = make_callback("lib:page:2")

    await lib_handler.on_lib_legacy(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_STALE)
    context.bot.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("fn_name,data", [
    ("on_library_callback", "nav:library"),
    ("on_lib_destination", "lib:latest"),
    ("on_lib_sort", "lib:sort:az"),
    ("on_lib_page", "lib:page:latest:2"),
    ("on_lib_search_page", "lib:spg:ab12cd34:2"),
    ("on_lib_row", "lib:get:ab12cd34:1"),
    ("on_lib_detail", "lib:dl:ab12cd34"),
    ("on_lib_search_start", "lib:search"),
    ("on_lib_search_new", "lib:scn"),
    ("on_lib_search_cancel", "lib:scx"),
    ("on_lib_legacy", "lib:dl:ab12cd34:1"),
])
async def test_group_gate_toasts_private(fn_name, data):
    """Nothing library-related is ever rendered into a group (plan §3):
    every v3 callback answers one toast and runs nothing."""
    update = make_callback(data, chat_id=-100, chat_type="supergroup")
    context = make_context()

    await getattr(lib_handler, fn_name)(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        copy.TOAST_LIB_PRIVATE)
    context.bot.send_message.assert_not_awaited()
    context.bot.send_audio.assert_not_awaited()
    context.bot.copy_message.assert_not_awaited()
    context.bot.edit_message_text.assert_not_awaited()


async def test_lib_search_link_handoff(monkeypatch):
    """A media URL pasted during the search prompt goes to the download
    flow and leaves the state (owner decision, plan §12.1.1)."""
    from bot.conversations import main as conv_main
    handed = []

    async def fake_handle_link(update, context):
        handed.append(update)
    monkeypatch.setattr(conv_main.dispatch, "handle_link",
                        fake_handle_link)

    update = make_text_update("https://open.spotify.com/track/abc")
    context = make_context()
    context.user_data[lib_handler.LIB_SEARCH_CARD_KEY] = (CHAT_ID, 600)

    state = await conv_main._lib_search_link(update, context)

    assert state == HOME_STATE
    assert handed == [update]
    assert lib_handler.LIB_SEARCH_CARD_KEY not in context.user_data


# --- keyboards / copy ---------------------------------------------------------------

def test_library_keyboards_layout():
    # M01 menu: four Phase A destinations (2 rows) + escape
    menu = keyboards.library_menu_keyboard()
    assert _cbs(menu) == [
        "lib:latest", "lib:search", "lib:often", "lib:browse",
        "nav:tasks", "nav:home"]

    # M02 list: one [▶ n] [⋯ n] row per track + page row + Menu + escape
    markup = keyboards.library_list_keyboard(
        "ab12cd34", source="browse", sort="az", page=2, pages=3, count=5)
    assert len(markup.inline_keyboard) == 8
    cbs = _cbs(markup)
    for i in range(1, 6):
        assert f"lib:get:ab12cd34:{i}" in cbs
        assert f"lib:more:ab12cd34:{i}" in cbs
    assert "lib:page:az:1" in cbs  # Previous
    assert "lib:page:az:3" in cbs  # Next
    assert "nav:library" in cbs

    # M06 search (multi-page): New search folds into the page row; the
    # buttons are numbered like the printed rows (6..10) and resolve
    # into the frozen FULL set
    markup = keyboards.library_search_results_keyboard(
        "ab12cd34", page=2, pages=3, count=5, start=6)
    rows = markup.inline_keyboard
    assert len(rows) == 8
    assert [b.callback_data for b in rows[0]] == [
        "lib:get:ab12cd34:6", "lib:more:ab12cd34:6"]
    assert [b.callback_data for b in rows[5]] == [
        "lib:spg:ab12cd34:1", "lib:scn", "lib:spg:ab12cd34:3"]

    # M06 search (single page): New search gets its own row
    markup = keyboards.library_search_results_keyboard(
        "ab12cd34", page=1, pages=1, count=2)
    assert len(markup.inline_keyboard) == 5
    cbs = _cbs(markup)
    assert "lib:scn" in cbs
    assert not any(cb.startswith("lib:spg:") for cb in cbs)

    # M05 detail: one action per row; the URL button carries no payload
    markup = keyboards.library_detail_keyboard("ab12cd34",
                                               spotify_url=TRACK_URL)
    rows = markup.inline_keyboard
    assert len(rows) == 7
    assert rows[2][0].url == TRACK_URL
    assert rows[2][0].callback_data is None
    assert len(keyboards.library_detail_keyboard("ab12cd34").inline_keyboard) == 6

    # M05b confirm: Yes / No / Menu / escape
    assert len(keyboards.library_remove_confirm_keyboard(
        "ab12cd34").inline_keyboard) == 4

    # M04 sort selection: 5 rows
    assert len(keyboards.library_sort_keyboard().inline_keyboard) == 5

    # M03 prompt: Cancel / Menu / escape
    assert _cbs(keyboards.library_search_prompt_keyboard()) == [
        "lib:scx", "nav:library", "nav:tasks", "nav:home"]

    # every v3 payload fits the Bot API 64-byte limit
    for markup in (
        keyboards.library_menu_keyboard(),
        keyboards.library_list_keyboard("ab12cd34", source="browse",
                                        sort="az", page=999, pages=999,
                                        count=5),
        keyboards.library_search_results_keyboard(
            "ab12cd34", page=999, pages=999, count=5),
        keyboards.library_search_none_keyboard(),
        keyboards.library_search_prompt_keyboard(),
        keyboards.library_sort_keyboard(),
        keyboards.library_detail_keyboard("ab12cd34", spotify_url=TRACK_URL),
        keyboards.library_remove_confirm_keyboard("ab12cd34"),
        keyboards.library_often_empty_keyboard(),
        keyboards.library_empty_keyboard(),
        keyboards.library_unavailable_keyboard(),
        keyboards.library_no_resend_keyboard(),
        keyboards.library_flood_wait_keyboard("lib:spg:ab12cd34:999"),
    ):
        for cb in _cbs(markup):
            assert len(cb.encode("utf-8")) <= 64, cb


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
