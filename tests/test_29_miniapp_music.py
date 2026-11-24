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
from models.track import Track
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


# --- SPA copy (test_08 pattern: pinned against the static file) -------------------

def test_miniapp_music_copy_matches_deck():
    """The SPA copy aligns with the chat deck (utils/copy.py LIB_*) —
    same states, same tone (plan §12.14). API-rendered errors (search
    too short, exhausted re-send) are pinned on their endpoint tests."""
    html = (Path(__file__).resolve().parent.parent / "web" / "static"
            / "index.html").read_text(encoding="utf-8")
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
    """The SPA must attach initData to EVERY library call.

    Regression (prod, 2026-11-24): musicUrl() and the track action calls
    omitted initData, so every /api/library request 401'd and the catch-all
    error branch rendered the misleading "temporarily unavailable" card.
    The server accepts initData from the query string (GETs) and the JSON
    body (POSTs) — the SPA must use one of the two on each call."""
    html = (Path(__file__).resolve().parent.parent / "web" / "static"
            / "index.html").read_text(encoding="utf-8")
    m = re.search(r"function musicUrl\(page\)\{.*?\n\}", html, re.S)
    assert m, "musicUrl() missing from the SPA"
    assert "initData" in m.group(0), "musicUrl() must carry initData"
    m = re.search(r"function pollResend\(key\)\{.*?\n\}", html, re.S)
    assert m, "pollResend() missing from the SPA"
    assert "initData" in m.group(0), "resend/status GET must carry initData"
    for call in (
        '"/api/library/track/" + key + "/resend",\n      { initData',
        '"/api/library/track/" + key + "/preview",\n      { initData',
        '"/api/library/track/" + key + "/remove",\n      { initData',
    ):
        assert call in html, f"track action call missing initData: {call!r}"
    assert 'showMusicState("no-auth")' in html, \
        "401s must render the no-auth state, not 'temporarily unavailable'"
