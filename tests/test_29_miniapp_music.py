"""#29 Mini App music library (docs/music-library-plan.md §7 / §12.14).

- auth matrix: the library routes are personal data — initData is
  MANDATORY (anonymous 401, forged 401), unlike /api/jobs
- GET /api/library view matrix over the shared facade: latest / often /
  browse (+3 sorts) / search (normalization, min-2-chars, zero results) /
  daily (owner add-on: the day list with Today/Yesterday labels + the
  per-day track page), paging + the out-of-range clamp, 503 for a DB
  failure (never looks like the empty library), owner isolation
- per-track actions: the re-send ladder from the app (shared core
  utils.resend: file_id -> copyMessage -> flood -> queued re-download ->
  exhausted; record_request count; the row's cloud handles refreshed by
  the re-download's delivery capture via record_file_ref), the 30s
  preview resolve, remove (not-found + DB-failure states)
- cover proxy: the DB row's own cover_url (never client-supplied),
  cache write + hit, the no-cover and fetch-failure states
- _redownload_spotify core: the app's ladder step 3 (spotdl primitives
  + send_audio + the staging cleanup)
- SPA copy pinned against the chat deck (test_08 pattern)

Every request signs in as the SAME tg_id the rows are seeded under:
the facade reads are per-user, so a mismatched initData is exactly the
"empty library" the owner-isolation tests rely on.
"""
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from telegram.error import BadRequest, RetryAfter

import bot.handlers.music.spotify.download as download
import utils.decorators as decorators
import utils.library as mlib
import web.app as webapp
from connectors.db import SessionLocal
from models.playlist import Playlist
from models.playlist_item import PlaylistItem
from models.tag import Tag
from models.track import Track
from models.track_tag import TrackTag
from tests.conftest import make_init_data, user_params

FORGED = make_init_data(user_params(), token="wrong:token")
COVER_URL = "https://i.scdn.co/image/abc123"
SPOTIFY_URL = "https://open.spotify.com/track/abc"


# --- fixtures ------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _reset_db_breaker():
    """The DB breaker is a module singleton shared by all soft-failing
    DB work; isolate tests (test_28 pattern)."""
    decorators._db_breaker.reset()
    yield
    await decorators.join_pending_db_work()
    decorators._db_breaker.reset()


def _fresh_tg_id() -> int:
    # tg_id is a 32-bit int in the schema: keep inside INT4 range
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


def init_for(tg_id: int) -> str:
    """A valid initData blob for the seeded user of this test."""
    return make_init_data(user_params(user_id=tg_id))


@pytest.fixture
def test_tg():
    tg_id = _fresh_tg_id()
    yield tg_id
    db = SessionLocal()
    # child tables first (track_tags/playlist_items), then the parents
    db.query(TrackTag).filter(TrackTag.tg_id == tg_id).delete(synchronize_session=False)
    db.query(PlaylistItem).filter(
        PlaylistItem.playlist_id.in_(
            db.query(Playlist.id).filter(Playlist.tg_id == tg_id))).delete(
        synchronize_session=False)
    db.query(Playlist).filter(Playlist.tg_id == tg_id).delete(synchronize_session=False)
    db.query(Tag).filter(Tag.tg_id == tg_id).delete(synchronize_session=False)
    db.query(Track).filter(Track.tg_id == tg_id).delete(synchronize_session=False)
    db.commit()
    db.close()


def seed_track(tg_id: int, key: str, *, title="Title", artist="Artist",
               album="", duration_sec=200, cover_url=None,
               spotify_url=None, file_id=None, orig_chat_id=None,
               orig_message_id=None, count=1, added=None, last=None):
    """One library row with explicit timestamps (the daily grouping's
    whole input surface)."""
    now = datetime.now()
    db = SessionLocal()
    try:
        row = Track(
            tg_id=tg_id, track_key=key, title=title, artist=artist,
            album=album, duration_sec=duration_sec, cover_url=cover_url,
            spotify_url=spotify_url, source="spotify", file_id=file_id,
            orig_chat_id=orig_chat_id, orig_message_id=orig_message_id,
            request_count=count,
            added_on=added or now - timedelta(days=3),
            last_requested=last or now,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()
    return row


def day_at(day: date, hour: int) -> datetime:
    """A fixed wall-clock instant on `day`.

    Day-grouping tests must not seed `now - timedelta(...)`: when the
    suite runs shortly after local midnight those offsets land on the
    PREVIOUS day and "today" assertions break (00:00-03:00 flake).
    Explicit in-day times are stable at any hour — a "today" row a few
    hours in the future is fine, grouping only reads the date."""
    return datetime(day.year, day.month, day.day, hour, 0)


def track_count(tg_id: int, key: str) -> int:
    db = SessionLocal()
    try:
        return (db.query(Track.request_count)
                .filter(Track.tg_id == tg_id, Track.track_key == key)
                .scalar() or 0)
    finally:
        db.close()


def track_file_id(tg_id: int, key: str):
    db = SessionLocal()
    try:
        return (db.query(Track.file_id)
                .filter(Track.tg_id == tg_id, Track.track_key == key)
                .scalar())
    finally:
        db.close()


@pytest.fixture
async def client(web_app):
    app, fake_bot = web_app
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, fake_bot, app
    await c.close()


# --- facade: daily (owner add-on) ------------------------------------------------

def test_daily_days_orders_and_counts(test_tg):
    today = datetime.now().date()
    seed_track(test_tg, "t1", last=day_at(today, 10))
    seed_track(test_tg, "t2", last=day_at(today, 12))
    seed_track(test_tg, "t3", last=day_at(today - timedelta(days=1), 12))
    seed_track(test_tg, "t4", last=day_at(today - timedelta(days=5), 12))
    days = mlib.daily_days_sync(test_tg)
    assert [d for d, _c in days] == [
        today,
        today - timedelta(days=1),
        today - timedelta(days=5),
    ]
    assert [c for _d, c in days] == [2, 1, 1]


def test_daily_page_orders_newest_first_and_clamps(test_tg):
    today = datetime.now().date()
    seed_track(test_tg, "a", last=day_at(today, 9))
    seed_track(test_tg, "b", last=day_at(today, 10))
    seed_track(test_tg, "c", last=day_at(today, 11))
    tracks, total = mlib.daily_page_sync(test_tg, today, page=1, page_size=2)
    assert total == 3
    assert [t.track_key for t in tracks] == ["c", "b"]
    # a page past the end falls back to page 1 (the rows may have moved)
    tracks, _ = mlib.daily_page_sync(test_tg, today, page=9, page_size=2)
    assert [t.track_key for t in tracks] == ["c", "b"]


def test_daily_uses_last_requested_not_added_on(test_tg):
    """A re-request moves the track to the NEWER day (plan §12.14:
    last_requested is the day signal; no per-request log exists)."""
    today = datetime.now().date()
    old_day = today - timedelta(days=4)
    seed_track(test_tg, "moved",
               added=day_at(old_day, 9),
               last=day_at(today, 10))
    days = mlib.daily_days_sync(test_tg)
    assert [d for d, _c in days] == [today]


def test_day_label_today_yesterday_month_year():
    now = datetime.now()
    assert mlib.day_label(now.date()) == "Today"
    assert mlib.day_label(now.date() - timedelta(days=1)) == "Yesterday"
    some = datetime(2026, 11, 18).date()
    expected = "Nov 18" if some.year == now.year else "Nov 18, 2026"
    assert mlib.day_label(some) == expected
    assert mlib.day_label(datetime(2025, 1, 1).date()) == "Jan 1, 2025"


def test_track_day_falls_back_to_added_on():
    now = datetime.now()
    t = mlib.LibraryTrack(
        track_key="k", title="T", artist="", album="", duration_sec=None,
        cover_url=None, spotify_url=None, source="spotify", file_id=None,
        orig_chat_id=None, orig_message_id=None, request_count=1,
        added_on=now - timedelta(days=2), last_requested=None,
    )
    assert mlib._track_day(t) == (now - timedelta(days=2)).date()


# --- auth matrix -----------------------------------------------------------------

async def test_library_anonymous_401(client):
    c, _, _ = client
    r = await c.get("/api/library")
    assert r.status == 401
    r = await c.post("/api/library/track/k/resend", json={})
    assert r.status == 401
    r = await c.get("/api/library/cover/k")
    assert r.status == 401
    r = await c.post("/api/library/track/k/remove", json={})
    assert r.status == 401
    r = await c.get("/api/library/track/k/resend/status")
    assert r.status == 401
    r = await c.post("/api/library/track/k/preview", json={})
    assert r.status == 401


async def test_library_forged_401(client):
    c, _, _ = client
    r = await c.get("/api/library", params={"initData": FORGED})
    assert r.status == 401
    r = await c.get("/api/library/cover/k", params={"initData": FORGED})
    assert r.status == 401


# --- GET /api/library ------------------------------------------------------------

async def test_library_latest_default_shape(client, test_tg):
    c, _, _ = client
    now = datetime.now()
    ini = init_for(test_tg)
    seed_track(test_tg, "old", title="Old Song", artist="A",
               duration_sec=236, cover_url=COVER_URL,
               last=now - timedelta(days=1))
    seed_track(test_tg, "new", title="New Song", artist="B",
               duration_sec=65, last=now - timedelta(minutes=2))
    r = await c.get("/api/library", params={"initData": ini})
    assert r.status == 200
    body = await r.json()
    assert body["view"] == "latest"
    assert body["total"] == 2
    assert body["pages"] == 1
    assert [t["track_key"] for t in body["tracks"]] == ["new", "old"]
    t = body["tracks"][0]
    assert t["title"] == "New Song"
    assert t["duration"] == "1:05"
    assert t["has_cover"] is False
    assert t["has_stream"] is False  # no file_id seeded -> preview only
    assert t["request_count"] == 1
    # timestamps carry the server's UTC offset (naive-local -> aware)
    assert "+" in t["last_requested"]
    assert t["spotify_url"] is None
    t = body["tracks"][1]
    assert t["has_cover"] is True


async def test_library_empty_is_200_not_503(client, test_tg):
    c, _, _ = client
    r = await c.get("/api/library", params={"initData": init_for(test_tg)})
    assert r.status == 200
    body = await r.json()
    assert body["total"] == 0
    assert body["tracks"] == []


async def test_library_often_only_multi_requested(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "once", count=1)
    seed_track(test_tg, "thrice", count=3,
               last=datetime.now() - timedelta(hours=1))
    r = await c.get("/api/library",
                    params={"initData": ini, "view": "often"})
    body = await r.json()
    assert [t["track_key"] for t in body["tracks"]] == ["thrice"]
    assert body["total"] == 1


@pytest.mark.parametrize("sort,keys", [
    ("az", ["alpha", "beta"]),          # "Alpha" < "Beta"
    ("recent", ["beta", "alpha"]),      # beta requested more recently
    ("added", ["alpha", "beta"]),       # alpha added later
])
async def test_library_browse_sorts(client, test_tg, sort, keys):
    c, _, _ = client
    ini = init_for(test_tg)
    now = datetime.now()
    seed_track(test_tg, "alpha", title="Alpha",
               added=now - timedelta(days=1),
               last=now - timedelta(hours=5))
    seed_track(test_tg, "beta", title="Beta",
               added=now - timedelta(days=2),
               last=now - timedelta(minutes=5))
    r = await c.get("/api/library",
                    params={"initData": ini, "view": "browse", "sort": sort})
    assert r.status == 200
    body = await r.json()
    assert body["sort"] == sort
    assert [t["track_key"] for t in body["tracks"]] == keys


async def test_library_search_matches_and_normalizes(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "s1", title="Che Saboure Dele Man", artist="Simin Ghanem")
    seed_track(test_tg, "s2", title="Other Song", artist="Nobody")
    r = await c.get("/api/library", params={
        "initData": ini, "view": "search", "q": "  simin  "})
    assert r.status == 200
    body = await r.json()
    assert body["query"] == "  simin  "
    assert [t["track_key"] for t in body["tracks"]] == ["s1"]


async def test_library_search_too_short_400(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    r = await c.get("/api/library",
                    params={"initData": ini, "view": "search", "q": "a"})
    assert r.status == 400
    # the exact deck line (LIB_SEARCH_TOO_SHORT), not just a hint
    assert (await r.json())["error"] == "Please enter at least 2 characters."


async def test_library_search_zero_results(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "s1", title="Alpha", artist="A")
    r = await c.get("/api/library",
                    params={"initData": ini, "view": "search", "q": "zzz"})
    assert r.status == 200
    body = await r.json()
    assert body["total"] == 0
    assert body["tracks"] == []


async def test_library_search_pages_the_full_set(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    for i in range(5):
        seed_track(test_tg, f"k{i}", title=f"Shared {i}", artist="One")
    r = await c.get("/api/library", params={
        "initData": ini, "view": "search", "q": "shared", "page_size": 2})
    body = await r.json()
    assert body["total"] == 5
    assert body["pages"] == 3
    assert len(body["tracks"]) == 2
    r = await c.get("/api/library", params={
        "initData": ini, "view": "search", "q": "shared",
        "page_size": 2, "page": 3})
    body = await r.json()
    assert body["page"] == 3
    assert len(body["tracks"]) == 1


async def test_library_paging_and_clamp(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    now = datetime.now()
    for i in range(5):
        seed_track(test_tg, f"k{i}", title=f"T{i}",
                   last=now - timedelta(minutes=i))
    r = await c.get("/api/library", params={
        "initData": ini, "view": "latest", "page_size": 2})
    body = await r.json()
    assert body["pages"] == 3
    assert len(body["tracks"]) == 2
    # a page past the end clamps back to 1 (the facade's contract)
    r = await c.get("/api/library", params={
        "initData": ini, "view": "latest", "page_size": 2, "page": 99})
    body = await r.json()
    assert body["page"] == 1


async def test_library_bad_params_400(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    for params in (
        {"view": "nope"},
        {"view": "browse", "sort": "nope"},
        {"view": "latest", "page": "x"},
        {"view": "daily", "day": "not-a-date"},
    ):
        r = await c.get("/api/library", params={"initData": ini, **params})
        assert r.status == 400, params


async def test_library_daily_day_list_and_day_page(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    today = datetime.now().date()
    seed_track(test_tg, "today1", last=day_at(today, 14))
    seed_track(test_tg, "today2", last=day_at(today, 10))
    seed_track(test_tg, "yest", last=day_at(today - timedelta(days=1), 12))

    r = await c.get("/api/library", params={"initData": ini, "view": "daily"})
    assert r.status == 200
    body = await r.json()
    assert body["tracks"] == []
    assert body["total"] == 0
    assert [d["label"] for d in body["days"]] == ["Today", "Yesterday"]
    assert [d["count"] for d in body["days"]] == [2, 1]
    today = body["days"][0]["day"]

    r = await c.get("/api/library", params={
        "initData": ini, "view": "daily", "day": today})
    body = await r.json()
    assert body["day"] == today
    assert body["total"] == 2
    assert [t["track_key"] for t in body["tracks"]] == ["today1", "today2"]
    # the day list still rides the response (the SPA's back path)
    assert body["days"][0]["day"] == today


async def test_library_daily_empty_library(client, test_tg):
    c, _, _ = client
    r = await c.get("/api/library",
                    params={"initData": init_for(test_tg), "view": "daily"})
    body = await r.json()
    assert body["days"] == []
    assert body["total"] == 0


async def test_library_503_when_db_down(client, test_tg, monkeypatch):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "k1")
    monkeypatch.setattr(mlib, "library_page",
                        AsyncMock(return_value=None))
    r = await c.get("/api/library", params={"initData": ini})
    assert r.status == 503
    assert "temporarily unavailable" in (await r.json())["error"]


async def test_library_owner_isolation(client, test_tg):
    """Rows of another tg_id are invisible (and unreachable by key)."""
    c, _, _ = client
    ini = init_for(test_tg)
    foreign = _fresh_tg_id()
    seed_track(test_tg, "mine", title="Mine")
    seed_track(foreign, "theirs", title="Theirs")
    try:
        r = await c.get("/api/library", params={"initData": ini})
        body = await r.json()
        assert [t["track_key"] for t in body["tracks"]] == ["mine"]
        # a foreign track_key is a plain 404 — never a leak
        r = await c.get("/api/library/cover/theirs",
                        params={"initData": ini})
        assert r.status == 404
    finally:
        db = SessionLocal()
        db.query(Track).filter(Track.tg_id == foreign).delete(
            synchronize_session=False)
        db.commit()
        db.close()


# --- resend (the plan §3 ladder from the app) ------------------------------------

async def test_resend_via_file_id(client, test_tg):
    c, fake_bot, _ = client
    seed_track(test_tg, "k1", file_id="AgCloud", count=1)
    r = await c.post("/api/library/track/k1/resend",
                     json={"initData": init_for(test_tg)})
    assert r.status == 200
    assert (await r.json()) == {"state": "sent", "via": "file_id"}
    kw = fake_bot.send_audio.await_args.kwargs
    assert kw["chat_id"] == test_tg
    assert kw["audio"] == "AgCloud"
    await decorators.join_pending_db_work()
    assert track_count(test_tg, "k1") == 2  # the re-send counted


async def test_resend_falls_back_to_copy_message(client, test_tg):
    c, fake_bot, _ = client
    seed_track(test_tg, "k1", file_id="AgDead",
               orig_chat_id=test_tg, orig_message_id=777)
    fake_bot.send_audio = AsyncMock(side_effect=BadRequest("gone"))
    r = await c.post("/api/library/track/k1/resend",
                     json={"initData": init_for(test_tg)})
    assert r.status == 200
    assert (await r.json())["via"] == "copied"
    kw = fake_bot.copy_message.await_args.kwargs
    assert kw["from_chat_id"] == test_tg
    assert kw["message_id"] == 777
    await decorators.join_pending_db_work()
    assert track_count(test_tg, "k1") == 2


async def test_resend_flood_429(client, test_tg):
    c, fake_bot, _ = client
    seed_track(test_tg, "k1", file_id="AgCloud")
    fake_bot.send_audio = AsyncMock(side_effect=RetryAfter(12))
    r = await c.post("/api/library/track/k1/resend",
                     json={"initData": init_for(test_tg)})
    assert r.status == 429
    assert "12 seconds" in (await r.json())["error"]
    await decorators.join_pending_db_work()
    assert track_count(test_tg, "k1") == 1  # nothing delivered, nothing counted


async def test_resend_redownload_queued(client, test_tg, monkeypatch):
    c, _, app = client
    app["spotdl"] = object()  # the re-download engine is up
    seed_track(test_tg, "k1", file_id=None, spotify_url=SPOTIFY_URL)
    calls = []

    async def fake_redownload(app_, tg, track, status):
        calls.append((track.track_key, tg))
        status["state"] = "sent"

    monkeypatch.setattr(webapp, "_redownload_spotify", fake_redownload)
    r = await c.post("/api/library/track/k1/resend",
                     json={"initData": init_for(test_tg)})
    assert r.status == 200
    assert (await r.json()) == {"state": "queued"}
    assert calls == [("k1", test_tg)]
    r = await c.get("/api/library/track/k1/resend/status",
                    params={"initData": init_for(test_tg)})
    assert (await r.json())["state"] == "sent"


async def test_resend_in_progress_409(client, test_tg):
    c, _, app = client
    seed_track(test_tg, "k1", file_id=None, spotify_url=SPOTIFY_URL)
    app["resends"][f"{test_tg}:k1"] = {
        "state": "downloading", "error": None, "created": time.time()}
    r = await c.post("/api/library/track/k1/resend",
                     json={"initData": init_for(test_tg)})
    assert r.status == 409
    assert "Already working" in (await r.json())["error"]


async def test_resend_exhausted_409(client, test_tg):
    c, _, _ = client
    seed_track(test_tg, "k1", file_id=None, spotify_url=None)
    r = await c.post("/api/library/track/k1/resend",
                     json={"initData": init_for(test_tg)})
    assert r.status == 409
    # the deck's LIB_NO_RESEND core line
    assert (await r.json())["error"] == (
        "We couldn't retrieve this track. It may no longer "
        "be available from its source.")


async def test_resend_unknown_or_foreign_404(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    foreign = _fresh_tg_id()
    seed_track(foreign, "foreign", file_id="AgX")
    try:
        r = await c.post("/api/library/track/ghost/resend",
                         json={"initData": ini})
        assert r.status == 404
        # another owner's row is unreachable by key
        r = await c.post("/api/library/track/foreign/resend",
                         json={"initData": ini})
        assert r.status == 404
    finally:
        db = SessionLocal()
        db.query(Track).filter(Track.tg_id == foreign).delete(
            synchronize_session=False)
        db.commit()
        db.close()


async def test_resend_status_404_without_flight(client, test_tg):
    c, _, _ = client
    seed_track(test_tg, "k1")
    r = await c.get("/api/library/track/k1/resend/status",
                    params={"initData": init_for(test_tg)})
    assert r.status == 404


# --- _redownload_spotify core (ladder step 3) ------------------------------------

def _fake_song(cover="https://i.scdn.co/image/x"):
    return SimpleNamespace(cover_url=cover, name="T", artist="A")


async def test_redownload_delivers_and_refreshes_row(client, test_tg,
                                                     monkeypatch, tmp_path):
    c, fake_bot, app = client
    app["spotdl"] = object()
    seed_track(test_tg, "k1", file_id=None, spotify_url=SPOTIFY_URL,
               count=1)
    track = await mlib.get_library_track(test_tg, "k1")
    mp3 = tmp_path / "re.mp3"
    mp3.write_bytes(b"fake")
    song = _fake_song()

    monkeypatch.setattr(download, "search_term",
                        lambda spotdl, url: [song])
    monkeypatch.setattr(
        download, "await_download_from_spotify",
        AsyncMock(return_value=(song, str(mp3))))
    message = SimpleNamespace(
        audio=SimpleNamespace(file_id="AgNew"), message_id=555)
    fake_bot.send_audio = AsyncMock(return_value=message)
    cleaned = []
    monkeypatch.setattr(webapp, "cleanup_media", cleaned.append)

    entry = {"state": "resolving", "error": None, "created": 0}
    await webapp._redownload_spotify(app, test_tg, track, entry)

    assert entry["state"] == "sent"
    kw = fake_bot.send_audio.await_args.kwargs
    assert kw["chat_id"] == test_tg
    assert kw["title"] == "Title"
    assert cleaned == [str(mp3)]  # staging never outlives the send
    await decorators.join_pending_db_work()
    assert track_file_id(test_tg, "k1") == "AgNew"  # handles refreshed
    assert track_count(test_tg, "k1") == 2          # counted as a request


async def test_redownload_unresolvable_link(client, test_tg, monkeypatch):
    c, fake_bot, app = client
    app["spotdl"] = object()
    seed_track(test_tg, "k1", file_id=None, spotify_url=SPOTIFY_URL)
    track = await mlib.get_library_track(test_tg, "k1")
    monkeypatch.setattr(download, "search_term", lambda spotdl, url: [])
    entry = {"state": "resolving", "error": None, "created": 0}
    await webapp._redownload_spotify(app, test_tg, track, entry)
    assert entry["state"] == "failed"
    assert "no longer resolves" in entry["error"]
    fake_bot.send_audio.assert_not_awaited()


async def test_redownload_failure_marks_failed_and_cleans(client, test_tg,
                                                          monkeypatch,
                                                          tmp_path):
    c, fake_bot, app = client
    app["spotdl"] = object()
    seed_track(test_tg, "k1", file_id=None, spotify_url=SPOTIFY_URL)
    track = await mlib.get_library_track(test_tg, "k1")
    mp3 = tmp_path / "re.mp3"
    mp3.write_bytes(b"fake")
    monkeypatch.setattr(download, "search_term",
                        lambda spotdl, url: [_fake_song()])
    monkeypatch.setattr(
        download, "await_download_from_spotify",
        AsyncMock(return_value=(_fake_song(), str(mp3))))
    fake_bot.send_audio = AsyncMock(side_effect=RuntimeError("egress"))
    cleaned = []
    monkeypatch.setattr(webapp, "cleanup_media", cleaned.append)
    entry = {"state": "resolving", "error": None, "created": 0}
    await webapp._redownload_spotify(app, test_tg, track, entry)
    assert entry["state"] == "failed"
    assert cleaned == [str(mp3)]
    await decorators.join_pending_db_work()
    assert track_count(test_tg, "k1") == 1  # failed send is not counted


# --- preview ---------------------------------------------------------------------

async def test_preview_returns_url(client, test_tg, monkeypatch):
    c, _, _ = client
    seed_track(test_tg, "k1", title="Some Song", artist="Someone")
    monkeypatch.setattr(
        webapp, "resolve_preview_url",
        AsyncMock(return_value="http://preview.example/30.mp3"))
    r = await c.post("/api/library/track/k1/preview",
                     json={"initData": init_for(test_tg)})
    assert r.status == 200
    assert (await r.json()) == {"url": "http://preview.example/30.mp3"}
    assert webapp.resolve_preview_url.await_args.args == (
        "Some Song", "Someone")


async def test_preview_none_404(client, test_tg, monkeypatch):
    c, _, _ = client
    seed_track(test_tg, "k1")
    monkeypatch.setattr(webapp, "resolve_preview_url",
                        AsyncMock(return_value=None))
    r = await c.post("/api/library/track/k1/preview",
                     json={"initData": init_for(test_tg)})
    assert r.status == 404
    assert (await r.json())["error"] == (
        "No 30s preview found for this track.")


async def test_preview_unknown_track_404(client, test_tg):
    c, _, _ = client
    r = await c.post("/api/library/track/ghost/preview",
                     json={"initData": init_for(test_tg)})
    assert r.status == 404


# --- remove ----------------------------------------------------------------------

async def test_remove_deletes_row(client, test_tg):
    c, _, _ = client
    seed_track(test_tg, "k1")
    r = await c.post("/api/library/track/k1/remove",
                     json={"initData": init_for(test_tg)})
    assert r.status == 200
    assert (await r.json()) == {"ok": True}
    assert track_count(test_tg, "k1") == 0


async def test_remove_unknown_404(client, test_tg):
    c, _, _ = client
    r = await c.post("/api/library/track/ghost/remove",
                     json={"initData": init_for(test_tg)})
    assert r.status == 404
    assert "no longer in your library" in (await r.json())["error"]


async def test_remove_db_failure_503(client, test_tg, monkeypatch):
    c, _, _ = client
    seed_track(test_tg, "k1")
    monkeypatch.setattr(mlib, "remove_track", AsyncMock(return_value=False))
    r = await c.post("/api/library/track/k1/remove",
                     json={"initData": init_for(test_tg)})
    assert r.status == 503
    assert "try again" in (await r.json())["error"]


# --- tag filter (?tag=) -------------------------------------------------------------

def _tag_track(tg_id: int, track_key: str, name: str) -> None:
    """Seed one tag + link straight through the crud layer (the
    endpoint tests exercise the HTTP surface separately)."""
    db = SessionLocal()
    try:
        import cruds.library as cruds

        tag = cruds.find_tag(db, tg_id, name)
        if tag is None:
            tag = cruds.create_tag(db, tg_id, name)
        db.add(TrackTag(tg_id=tg_id, track_key=track_key, tag_id=tag.id))
        db.commit()
    finally:
        db.close()


async def test_library_tag_filter_latest(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "a", title="Alpha")
    seed_track(test_tg, "b", title="Beta")
    seed_track(test_tg, "c", title="Gamma")
    _tag_track(test_tg, "a", "Rock")
    _tag_track(test_tg, "c", "Rock")
    r = await c.get("/api/library",
                    params={"initData": ini, "tag": "Rock"})
    assert r.status == 200
    body = await r.json()
    assert body["tag"] == "Rock"
    assert body["total"] == 2
    assert {t["track_key"] for t in body["tracks"]} == {"a", "c"}
    # the track JSON embeds the chip row (v2 §4.2: the sheet reads it
    # from the page, no second request)
    by_key = {t["track_key"]: t for t in body["tracks"]}
    assert by_key["a"]["tags"] == ["Rock"]
    assert by_key["c"]["tags"] == ["Rock"]
    # the filter is case-insensitive on both sides
    r = await c.get("/api/library",
                    params={"initData": ini, "tag": "rock"})
    assert (await r.json())["total"] == 2


async def test_library_tag_filter_unknown_tag_is_empty_not_error(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "a")
    r = await c.get("/api/library",
                    params={"initData": ini, "tag": "ghost-tag"})
    assert r.status == 200
    body = await r.json()
    assert body["total"] == 0
    assert body["tracks"] == []


async def test_library_tag_filter_search_and_daily(client, test_tg):
    today = datetime.now().date()
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "a", title="Stoned Pilsner", last=day_at(today, 9))
    seed_track(test_tg, "b", title="Stoned Soup", last=day_at(today, 10))
    _tag_track(test_tg, "a", "Fave")
    r = await c.get("/api/library", params={
        "initData": ini, "view": "search", "q": "stoned", "tag": "Fave"})
    body = await r.json()
    assert body["total"] == 1
    assert body["tracks"][0]["track_key"] == "a"
    r = await c.get("/api/library", params={
        "initData": ini, "view": "daily", "day": today.isoformat(),
        "tag": "Fave"})
    body = await r.json()
    assert body["total"] == 1
    assert body["tracks"][0]["track_key"] == "a"


async def test_library_tag_filter_503_when_db_down(client, test_tg,
                                                   monkeypatch):
    c, _, _ = client
    seed_track(test_tg, "a")
    monkeypatch.setattr(mlib, "tagged_track_keys",
                        AsyncMock(return_value=None))
    r = await c.get("/api/library",
                    params={"initData": init_for(test_tg), "tag": "x"})
    assert r.status == 503


# --- playlists (spec §4.2) -----------------------------------------------------------

async def test_playlist_create_list(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    r = await c.post("/api/library/playlists",
                     json={"initData": ini, "name": "Road Trip"})
    assert r.status == 201
    created = await r.json()
    assert created["name"] == "Road Trip"
    assert created["count"] == 0
    r = await c.get("/api/library/playlists", params={"initData": ini})
    body = await r.json()
    assert [p["name"] for p in body["playlists"]] == ["Road Trip"]


async def test_playlist_create_duplicate_is_409_case_insensitive(client,
                                                                 test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    assert (await c.post("/api/library/playlists",
                         json={"initData": ini, "name": "Road Trip"})).status == 201
    r = await c.post("/api/library/playlists",
                     json={"initData": ini, "name": "road trip"})
    assert r.status == 409
    # and the ORIGINAL case survives (no shadow row)
    body = await (await c.get("/api/library/playlists",
                              params={"initData": ini})).json()
    assert [p["name"] for p in body["playlists"]] == ["Road Trip"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
async def test_playlist_create_bad_name_400(client, test_tg, name):
    c, _, _ = client
    r = await c.post("/api/library/playlists",
                     json={"initData": init_for(test_tg), "name": name})
    assert r.status == 400


async def test_playlist_rename(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    pid = (await (await c.post("/api/library/playlists",
                               json={"initData": ini, "name": "One"}))
           .json())["id"]
    r = await c.patch(f"/api/library/playlists/{pid}",
                      json={"initData": ini, "name": "Two"})
    assert r.status == 200
    assert (await r.json())["name"] == "Two"
    # rename onto a case-variant of another playlist's name -> 409
    await c.post("/api/library/playlists",
                 json={"initData": ini, "name": "other"})
    r = await c.patch(f"/api/library/playlists/{pid}",
                      json={"initData": ini, "name": "OTHER"})
    assert r.status == 409
    # unknown id -> 404; bad name -> 400
    assert (await c.patch("/api/library/playlists/999999",
                          json={"initData": ini, "name": "X"})).status == 404
    assert (await c.patch(f"/api/library/playlists/{pid}",
                          json={"initData": ini, "name": ""})).status == 400


async def test_playlist_tracks_add_idempotent_and_paged(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    for k in ("t1", "t2", "t3"):
        seed_track(test_tg, k, title=k.upper())
    pid = (await (await c.post("/api/library/playlists",
                               json={"initData": ini, "name": "P"}))
           .json())["id"]
    # adding a member twice counts once
    r = await c.post(f"/api/library/playlists/{pid}/tracks",
                     json={"initData": ini, "track_keys": ["t1", "t2", "t1"]})
    assert r.status == 200
    assert (await r.json()) == {"added": 2, "unknown": 0}
    # a key with no library row is reported, not 404 (batch semantics)
    r = await c.post(f"/api/library/playlists/{pid}/tracks",
                     json={"initData": ini, "track_keys": ["ghost", "t3"]})
    assert (await r.json()) == {"added": 1, "unknown": 1}
    r = await c.get(f"/api/library/playlists/{pid}/tracks",
                    params={"initData": ini, "page_size": 2})
    body = await r.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert [t["track_key"] for t in body["tracks"]] == ["t1", "t2"]
    r = await c.get(f"/api/library/playlists/{pid}/tracks",
                    params={"initData": ini, "page": 2, "page_size": 2})
    assert [t["track_key"]
            for t in (await r.json())["tracks"]] == ["t3"]


async def test_playlist_track_remove(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "t1")
    pid = (await (await c.post("/api/library/playlists",
                               json={"initData": ini, "name": "P"}))
           .json())["id"]
    await c.post(f"/api/library/playlists/{pid}/tracks",
                 json={"initData": ini, "track_keys": ["t1"]})
    r = await c.delete(f"/api/library/playlists/{pid}/tracks/t1",
                       params={"initData": ini})
    assert r.status == 200
    assert (await r.json()) == {"ok": True}
    # again: not a member anymore -> 404
    r = await c.delete(f"/api/library/playlists/{pid}/tracks/t1",
                       params={"initData": ini})
    assert r.status == 404
    # the track itself stayed in the library
    assert (await (await c.get("/api/library", params={"initData": ini}))
            .json())["total"] == 1


async def test_playlist_delete_keeps_tracks(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "t1")
    pid = (await (await c.post("/api/library/playlists",
                               json={"initData": ini, "name": "P"}))
           .json())["id"]
    await c.post(f"/api/library/playlists/{pid}/tracks",
                 json={"initData": ini, "track_keys": ["t1"]})
    r = await c.delete(f"/api/library/playlists/{pid}",
                       params={"initData": ini})
    assert r.status == 200
    # gone for this owner
    assert (await c.get(f"/api/library/playlists/{pid}/tracks",
                        params={"initData": ini})).status == 404
    # the track survived
    assert (await (await c.get("/api/library", params={"initData": ini}))
            .json())["total"] == 1
    # unknown playlist -> 404
    assert (await c.delete("/api/library/playlists/999999",
                           params={"initData": ini})).status == 404


async def test_playlist_owner_isolation(client, test_tg):
    other = _fresh_tg_id()
    c, _, _ = client
    ini = init_for(test_tg)
    pid = (await (await c.post("/api/library/playlists",
                               json={"initData": ini, "name": "P"}))
           .json())["id"]
    other_ini = init_for(other)
    # the other owner neither sees nor can touch it
    body = await (await c.get("/api/library/playlists",
                              params={"initData": other_ini})).json()
    assert body["playlists"] == []
    assert (await c.patch(f"/api/library/playlists/{pid}",
                          json={"initData": other_ini,
                                "name": "Stolen"})).status == 404
    assert (await c.delete(f"/api/library/playlists/{pid}",
                           params={"initData": other_ini})).status == 404
    # cleanup the second owner's rows
    db = SessionLocal()
    db.query(Track).filter(Track.tg_id == other).delete(synchronize_session=False)
    db.commit()
    db.close()


async def test_playlist_503_when_db_down(client, test_tg, monkeypatch):
    c, _, _ = client
    monkeypatch.setattr(mlib, "playlist_list", AsyncMock(return_value=None))
    r = await c.get("/api/library/playlists",
                    params={"initData": init_for(test_tg)})
    assert r.status == 503
    monkeypatch.setattr(mlib, "playlist_create",
                        AsyncMock(return_value=None))
    r = await c.post("/api/library/playlists",
                     json={"initData": init_for(test_tg), "name": "X"})
    assert r.status == 503


# --- tags (spec §4.2) -----------------------------------------------------------------

async def test_tag_create_and_idempotent_variant(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    r = await c.post("/api/library/tags",
                     json={"initData": ini, "name": "Rock"})
    assert r.status == 201
    first = await r.json()
    # a case-variant returns the SAME tag (200, exists) — idempotent
    r = await c.post("/api/library/tags",
                     json={"initData": ini, "name": "rock"})
    assert r.status == 200
    again = await r.json()
    assert again["id"] == first["id"]
    body = await (await c.get("/api/library/tags",
                              params={"initData": ini})).json()
    assert len(body["tags"]) == 1
    assert body["tags"][0]["name"] == "Rock"


@pytest.mark.parametrize("name", ["", "  ", "x" * 81])
async def test_tag_create_bad_name_400(client, test_tg, name):
    c, _, _ = client
    r = await c.post("/api/library/tags",
                     json={"initData": init_for(test_tg), "name": name})
    assert r.status == 400


async def test_track_tags_merge(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "k1")
    r = await c.post("/api/library/track/k1/tags",
                     json={"initData": ini, "names": ["Rock", "jazz"]})
    assert r.status == 200
    assert (await r.json())["tags"] == ["jazz", "Rock"]  # A->Z on lower()
    # re-sending an existing name (case-variant) adds nothing
    r = await c.post("/api/library/track/k1/tags",
                     json={"initData": ini, "names": ["ROCK", "  ", "blues"]})
    assert (await r.json())["tags"] == ["blues", "jazz", "Rock"]
    # a track with no library row -> 404, bad input -> 400
    assert (await c.post("/api/library/track/ghost/tags",
                         json={"initData": ini, "names": ["x"]})) \
        .status == 404
    assert (await c.post("/api/library/track/k1/tags",
                         json={"initData": ini, "names": "nope"})) \
        .status == 400


async def test_track_tag_unlink(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "k1")
    await c.post("/api/library/track/k1/tags",
                 json={"initData": ini, "names": ["Rock", "jazz"]})
    # unlink is case-insensitive and answers with the FULL remaining
    # list (the sheet contract)
    r = await c.delete("/api/library/track/k1/tags/rock",
                       params={"initData": ini})
    assert r.status == 200
    assert (await r.json()) == {"ok": True, "tags": ["jazz"]}
    # idempotent: unlinking a tag that is not linked is not an error
    r = await c.delete("/api/library/track/k1/tags/rock",
                       params={"initData": ini})
    assert r.status == 200
    assert (await r.json())["tags"] == ["jazz"]
    # an unknown track is a 404 either way
    assert (await c.delete("/api/library/track/ghost/tags/rock",
                           params={"initData": ini})).status == 404


async def test_tag_delete_cascades_links(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "k1")
    tag_id = (await (await c.post("/api/library/tags",
                                  json={"initData": ini, "name": "Rock"}))
              .json())["id"]
    await c.post("/api/library/track/k1/tags",
                 json={"initData": ini, "names": ["Rock"]})
    r = await c.delete(f"/api/library/tags/{tag_id}",
                       params={"initData": ini})
    assert r.status == 200
    db = SessionLocal()
    try:
        left = (db.query(TrackTag)
                .filter(TrackTag.tg_id == test_tg).count())
        tags = (db.query(Tag).filter(Tag.tg_id == test_tg).count())
    finally:
        db.close()
    assert left == 0  # the link went with the tag
    assert tags == 0
    # unknown tag id -> 404
    assert (await c.delete("/api/library/tags/999999",
                           params={"initData": ini})).status == 404


async def test_tag_503_when_db_down(client, test_tg, monkeypatch):
    c, _, _ = client
    monkeypatch.setattr(mlib, "tag_list", AsyncMock(return_value=None))
    r = await c.get("/api/library/tags",
                    params={"initData": init_for(test_tg)})
    assert r.status == 503
    monkeypatch.setattr(mlib, "track_set_tags", AsyncMock(return_value=None))
    seed_track(test_tg, "k1")
    r = await c.post("/api/library/track/k1/tags",
                     json={"initData": init_for(test_tg), "names": ["x"]})
    assert r.status == 503


# --- remove cascades (spec §4.1 orphan pruning) --------------------------------------

async def test_remove_track_cascades_playlist_links_and_tags(client, test_tg):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "k1")
    pid = (await (await c.post("/api/library/playlists",
                               json={"initData": ini, "name": "P"}))
           .json())["id"]
    await c.post(f"/api/library/playlists/{pid}/tracks",
                 json={"initData": ini, "track_keys": ["k1"]})
    await c.post("/api/library/track/k1/tags",
                 json={"initData": ini, "names": ["Rock"]})
    assert (await c.post("/api/library/track/k1/remove",
                         json={"initData": ini})).status == 200
    db = SessionLocal()
    try:
        items = (db.query(PlaylistItem)
                 .filter(PlaylistItem.playlist_id == pid).count())
        links = (db.query(TrackTag)
                 .filter(TrackTag.tg_id == test_tg).count())
        tag_rows = (db.query(Tag).filter(Tag.tg_id == test_tg).count())
    finally:
        db.close()
    assert items == 0  # the playlist lost its member
    assert links == 0  # the tag lost its link
    assert tag_rows == 1  # the tag itself stays (count reads 0)


# --- stream: the Range proxy (spec §3) -------------------------------------------------

class _UpstreamResp:
    """Emulates the Telegram file endpoint over an in-memory body:
    Range-aware (200/206/416), the header set the real endpoint sends."""

    def __init__(self, data: bytes, headers: dict):
        self._full = data
        rng = (headers or {}).get("Range", "")
        self.status = 200
        self.headers = {"Content-Type": "application/octet-stream",
                        "Accept-Ranges": "bytes"}
        if rng.startswith("bytes="):
            spec = rng[6:]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else len(data) - 1
            if start >= len(data):
                self.status = 416
                self.headers["Content-Range"] = f"bytes */{len(data)}"
                self._data = b""
            else:
                self.status = 206
                end = min(end, len(data) - 1)
                self.headers["Content-Range"] = \
                    f"bytes {start}-{end}/{len(data)}"
                self._data = data[start:end + 1]
        else:
            self._data = data
        self.headers["Content-Length"] = str(len(self._data))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def content(self):
        data = self._data

        class _Content:
            async def iter_chunked(self, n):
                for i in range(0, len(data), n):
                    yield data[i:i + n]

        return _Content()

    def release(self):
        pass


class _Upstream:
    """A stand-in for the shared upstream aiohttp session."""

    def __init__(self, data: bytes = b""):
        self.data = data
        self.closed = False
        self.requests = []

    async def get(self, url, headers=None, timeout=None):
        self.requests.append(dict(headers or {}))
        return _UpstreamResp(self.data, headers)

    async def close(self):
        self.closed = True


def _fake_bot_file(fake_bot, path: str):
    """PTB 22.x get_file returns the FULL url as `file_path`."""
    from types import SimpleNamespace
    fake_bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_path=path))


async def test_stream_proxies_full_track(client, monkeypatch):
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1", file_id="CQACfake")
    upstream = _Upstream(b"0123456789")
    monkeypatch.setattr(webapp, "_stream_session", lambda a: upstream)
    _fake_bot_file(fake_bot,
                   "https://api.telegram.org/file/botx/music/file_1.mp3")
    r = await c.get("/api/library/track/k1/stream", params={"initData": ini})
    assert r.status == 200
    assert await r.read() == b"0123456789"
    assert r.headers["Accept-Ranges"] == "bytes"
    # the .mp3 path wins over the endpoint's octet-stream
    assert r.headers["Content-Type"] == "audio/mpeg"
    assert fake_bot.get_file.await_args.args[0] == "CQACfake"


async def test_stream_range_passthrough(client, monkeypatch):
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1", file_id="CQACfake")
    upstream = _Upstream(b"0123456789")
    monkeypatch.setattr(webapp, "_stream_session", lambda a: upstream)
    _fake_bot_file(fake_bot,
                   "https://api.telegram.org/file/botx/music/file_1.mp3")
    r = await c.get("/api/library/track/k1/stream",
                    params={"initData": ini},
                    headers={"Range": "bytes=2-5"})
    assert r.status == 206
    assert await r.read() == b"2345"
    assert r.headers["Content-Range"] == "bytes 2-5/10"
    assert r.headers["Content-Length"] == "4"
    # the client's Range reached the upstream verbatim
    assert upstream.requests[0]["Range"] == "bytes=2-5"


async def test_stream_range_beyond_end_416(client, monkeypatch):
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1", file_id="CQACfake")
    upstream = _Upstream(b"0123456789")
    monkeypatch.setattr(webapp, "_stream_session", lambda a: upstream)
    _fake_bot_file(fake_bot,
                   "https://api.telegram.org/file/botx/music/file_1.mp3")
    r = await c.get("/api/library/track/k1/stream",
                    params={"initData": ini},
                    headers={"Range": "bytes=99-"})
    assert r.status == 416


async def test_stream_no_file_id_404_no_source(client, monkeypatch):
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1")  # no file_id (backfilled / preview-only row)
    upstream = _Upstream(b"")
    monkeypatch.setattr(webapp, "_stream_session", lambda a: upstream)
    fake_bot.get_file = AsyncMock()
    r = await c.get("/api/library/track/k1/stream", params={"initData": ini})
    assert r.status == 404
    assert (await r.json()) == {"error": "no-source"}
    fake_bot.get_file.assert_not_awaited()


async def test_stream_getfile_failure_is_no_source(client, monkeypatch):
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1", file_id="CQACstale")
    monkeypatch.setattr(webapp, "_stream_session", lambda a: _Upstream())
    fake_bot.get_file = AsyncMock(side_effect=Exception("gone"))
    r = await c.get("/api/library/track/k1/stream", params={"initData": ini})
    assert r.status == 404
    assert (await r.json()) == {"error": "no-source"}


async def test_stream_upstream_404_is_no_source(client, monkeypatch):
    """Telegram purged the file after getFile succeeded (the 404
    anomaly): same contract as no file_id."""
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1", file_id="CQACfake")
    upstream = _Upstream(b'{"ok":false,"error_code":404,"description":"Not Found"}')
    upstream.data = b""
    # force the upstream to answer 404
    async def _get_404(url, headers=None, timeout=None):
        upstream.requests.append(dict(headers or {}))

        class _R:
            status = 404
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def release(self):
                pass
        return _R()
    monkeypatch.setattr(upstream, "get", _get_404)
    monkeypatch.setattr(webapp, "_stream_session", lambda a: upstream)
    _fake_bot_file(fake_bot,
                   "https://api.telegram.org/file/botx/music/file_1.mp3")
    r = await c.get("/api/library/track/k1/stream", params={"initData": ini})
    assert r.status == 404
    assert (await r.json()) == {"error": "no-source"}


async def test_stream_caches_file_path_per_file_id(client, monkeypatch):
    c, fake_bot, app = client
    ini = init_for(c_tg := _fresh_tg_id())
    seed_track(c_tg, "k1", file_id="CQACfake")
    upstream = _Upstream(b"abc")
    monkeypatch.setattr(webapp, "_stream_session", lambda a: upstream)
    _fake_bot_file(fake_bot,
                   "https://api.telegram.org/file/botx/music/file_1.mp3")
    for _ in range(2):
        r = await c.get("/api/library/track/k1/stream",
                        params={"initData": ini})
        assert r.status == 200
        await r.read()
    # one getFile for two plays (the ~1h cache)
    assert fake_bot.get_file.await_count == 1


async def test_stream_unknown_track_404(client, monkeypatch):
    c, fake_bot, app = client
    r = await c.get("/api/library/track/ghost/stream",
                    params={"initData": init_for(_fresh_tg_id())})
    assert r.status == 404


# --- cover proxy -------------------------------------------------------------------

async def test_cover_fetch_then_cache_hit(client, test_tg, monkeypatch,
                                          tmp_path):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "k1", cover_url=COVER_URL)
    cover_dir = tmp_path / "covers"
    monkeypatch.setattr(webapp, "COVER_DIR", cover_dir)
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return b"jpeg-bytes"

    monkeypatch.setattr(webapp, "_fetch_cover_image", fake_fetch)

    r = await c.get("/api/library/cover/k1", params={"initData": ini})
    assert r.status == 200
    assert r.headers["Content-Type"] == "image/jpeg"
    assert await r.read() == b"jpeg-bytes"
    assert fetched == [COVER_URL]
    assert any(cover_dir.glob("*.jpg"))  # cached on disk

    r = await c.get("/api/library/cover/k1", params={"initData": ini})
    assert r.status == 200
    assert await r.read() == b"jpeg-bytes"
    assert fetched == [COVER_URL]  # the second hit is the cache


async def test_cover_missing_or_failed(client, test_tg, monkeypatch,
                                       tmp_path):
    c, _, _ = client
    ini = init_for(test_tg)
    seed_track(test_tg, "noimg", cover_url=None)
    seed_track(test_tg, "badimg", cover_url=COVER_URL)
    monkeypatch.setattr(webapp, "COVER_DIR", tmp_path / "covers")
    r = await c.get("/api/library/cover/noimg", params={"initData": ini})
    assert r.status == 404
    monkeypatch.setattr(webapp, "_fetch_cover_image", lambda url: None)
    r = await c.get("/api/library/cover/badimg", params={"initData": ini})
    assert r.status == 502


async def test_cover_unknown_track_404(client, test_tg, monkeypatch, tmp_path):
    c, _, _ = client
    monkeypatch.setattr(webapp, "COVER_DIR", tmp_path / "covers")
    r = await c.get("/api/library/cover/ghost",
                    params={"initData": init_for(test_tg)})
    assert r.status == 404


# --- SPA copy (test_08 pattern: pinned against the React source tree) -------------

def _spa_source() -> str:
    """The React source tree, concatenated (v2: the SPA is React + TS in
    web/frontend/src, built by CI into the single inlined
    web/static/index.html). Copy is pinned against the source: JSX markup
    pins like ``>My music<`` only exist pre-compile. Test files are
    excluded so structural pins scan production code only."""
    root = Path(__file__).resolve().parent.parent / "web" / "frontend" / "src"
    parts = [
        p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix in (".ts", ".tsx")
        and "__tests__" not in p.parts
    ]
    assert parts, f"no React source found under {root}"
    return "\n".join(parts)


def test_miniapp_music_copy_matches_deck():
    """The SPA copy aligns with the chat deck (utils/copy.py LIB_*) —
    same states, same tone (plan §12.14). API-rendered errors (search
    too short, exhausted re-send) are pinned on their endpoint tests."""
    html = _spa_source()
    for s in (
        ">My music<",                                   # nav tab
        'data-view="daily">Daily<',                     # the owner's Daily menu
        "No music saved yet.",                          # S0 (LIB_NONE)
        "Your tracks will appear here automatically when you receive music.",
        "My music is temporarily unavailable.",         # S1 (LIB_UNAVAILABLE)
        "Please try again shortly.",
        "No matching tracks",                           # search (LIB_SEARCH_NONE)
        "Try another title or artist name.",
        "Often played",                                 # M07 (LIB_OFTEN_EMPTY)
        "Your frequently requested tracks will appear here as you listen again.",
        "Sent to your chat",                            # re-send success
        "Re-downloading — I'll send it to your chat when ready.",
        "Track removed from My music.",                 # TOAST_LIB_REMOVED
        "from your library? It does not delete previously sent messages.",
        "Open the app from Telegram to see your library.",
    ):
        assert s in html, f"missing Mini App music copy: {s!r}"


def test_miniapp_music_sends_init_data():
    """Every library call carries initData — structurally.

    Regression (prod, 2026-11-24): musicUrl() and the track action calls
    omitted initData, so every /api/library request 401'd and the catch-all
    error branch rendered the misleading "temporarily unavailable" card.

    v2 design: src/api/client.ts is the app's ONLY fetch site. api()
    injects initData into every POST JSON body (empty string outside
    Telegram), and withInit() appends it to GET query strings. A fetch
    anywhere else would bypass the injection, so this test pins the
    centralization: exactly one fetch( in the tree, in client.ts, with
    the encoding machinery — plus music.ts's error mapping (401/Telegram
    -> the no-auth state, never 'temporarily unavailable')."""
    root = Path(__file__).resolve().parent.parent / "web" / "frontend" / "src"
    client = (root / "api" / "client.ts").read_text(encoding="utf-8")
    fetch_sites = [
        str(p.relative_to(root))
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix in (".ts", ".tsx")
        and "__tests__" not in p.parts
        and "fetch(" in p.read_text(encoding="utf-8")
    ]
    assert fetch_sites == ["api/client.ts"], (
        "fetch( must exist only in api/client.ts, found: %r" % fetch_sites
    )
    assert "initData" in client and "encodeURIComponent" in client, (
        "client.ts must own the initData injection + query encoding"
    )
    music = (root / "music.ts").read_text(encoding="utf-8")
    assert "no-auth" in music, (
        "401s must render the no-auth state, not 'temporarily unavailable'"
    )
    assert re.search(r"Telegram\|Unauthorized", music), (
        "the 401/Telegram branch must stay in the load error mapping"
    )
