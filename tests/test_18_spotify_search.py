"""#18 Spotify text search & track cards (plan 1.2) — raw search mapping,
300s cache, card rendering, song-card callbacks, state wiring."""
import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import connectors.spotify_search as spotify_search
import bot.handlers.music.spotify.download as spotify_download
import bot.handlers.music.spotify.search as search
import spotdl.utils.spotify as spotdl_spotify
from utils import keyboards
from utils.constants.keyboards import SONG_ACTION_CALLBACK_PATTERN
from utils.constants.messages import (
    COMMING_SOON,
    NO_RESULTS_FOUND,
    SEARCH_FAILED,
    SONG_EXPIRED,
)
from utils.constants.states import MUSIC_SPOTIFY_STATE, MUSIC_STATE


def _fresh_tg_id() -> int:
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


def make_update(text, tg_id=None):
    update = MagicMock()
    msg = MagicMock()
    msg.text = text
    msg.chat_id = 7
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    update.effective_chat = MagicMock(id=7)
    update.effective_user = MagicMock(
        id=tg_id or _fresh_tg_id(), first_name="F", last_name="L", username="u"
    )
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    context.bot = MagicMock()
    for m in ("send_message", "send_chat_action", "send_video", "send_audio",
              "send_photo", "editMessageText", "deleteMessage",
              "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    return context


def make_callback(data):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    update.callback_query = cq
    update.effective_message = MagicMock(chat_id=7)
    return update


def fake_track(index=0, **kw):
    base = dict(
        title=f"Title {index}",
        artist=f"Artist {index}",
        album=f"Album {index}",
        duration_sec=214,
        spotify_url=f"https://open.spotify.com/track/track{index}",
        cover_url=f"https://i.scdn.co/image/{index}.jpg",
        preview_url=None,
    )
    base.update(kw)
    return spotify_search.TrackResult(**base)


@pytest.fixture(autouse=True)
def _clear_search_cache():
    spotify_search._cache.clear()
    yield
    spotify_search._cache.clear()


# --- raw search mapping (free-backend item -> TrackResult) ----------------------

class _FakeClient:
    def search(self, term):
        assert term == "starboy"
        return {"tracks": {"items": [
            {
                "name": "Starboy",
                "artists": [{"name": "The Weeknd"}, {"name": "Daft Punk"}],
                "album": {
                    "name": "Starboy",
                    "images": [
                        {"url": "small.jpg", "width": 64, "height": 64},
                        {"url": "big.jpg", "width": 640, "height": 640},
                        {"url": "mid.jpg", "width": 300, "height": 300},
                    ],
                },
                "duration_ms": 230000,
                "external_urls": {"spotify": "https://open.spotify.com/track/abc"},
            },
            {
                # degenerate item: no album, no images, no url, no duration
                "name": "Starboy (Remix)",
                "artists": [{"name": "Someone"}],
                "album": {},
            },
        ]}}


def test_raw_search_maps_items(monkeypatch):
    monkeypatch.setattr(spotdl_spotify, "SpotifyClient", _FakeClient)

    results = spotify_search._raw_search("starboy")

    assert len(results) == 2
    t0, t1 = results
    assert t0.title == "Starboy"
    assert t0.artist == "The Weeknd, Daft Punk"
    assert t0.album == "Starboy"
    assert t0.duration_sec == 230
    assert t0.spotify_url == "https://open.spotify.com/track/abc"
    assert t0.cover_url == "big.jpg"  # largest image wins
    assert t0.preview_url is None  # free backend carries no previews
    # degenerate item must not crash the mapping
    assert t1.title == "Starboy (Remix)"
    assert t1.album == ""
    assert t1.duration_sec == 0
    assert t1.spotify_url == ""
    assert t1.cover_url is None


def test_raw_search_handles_missing_tracks_key(monkeypatch):
    class _Empty:
        def search(self, term):
            return {}

    monkeypatch.setattr(spotdl_spotify, "SpotifyClient", _Empty)
    assert spotify_search._raw_search("nope") == []


# --- search_tracks: executor + 300s cache ---------------------------------------

async def test_search_tracks_caches_300s(monkeypatch):
    calls = []

    def raw(term):
        calls.append(term)
        return [fake_track(0)]

    monkeypatch.setattr(spotify_search, "_raw_search", raw)

    r1 = await spotify_search.search_tracks("Starboy")
    r2 = await spotify_search.search_tracks("starboy")  # case-insensitive hit

    assert calls == ["Starboy"]  # second call served from cache
    assert r2 is r1


async def test_search_tracks_refetches_after_ttl(monkeypatch):
    calls = []

    def raw(term):
        calls.append(term)
        return [fake_track(0)]

    monkeypatch.setattr(spotify_search, "_raw_search", raw)

    await spotify_search.search_tracks("starboy")
    ts, results = spotify_search._cache["starboy"]
    spotify_search._cache["starboy"] = (
        ts - spotify_search.CACHE_TTL_SECONDS - 1, results
    )

    await spotify_search.search_tracks("starboy")

    assert calls == ["starboy", "starboy"]


async def test_search_tracks_strips_query(monkeypatch):
    monkeypatch.setattr(
        spotify_search, "_raw_search", lambda term: [fake_track(0)]
    )
    await spotify_search.search_tracks("  starboy  ")
    assert "starboy" in spotify_search._cache


# --- format_duration -------------------------------------------------------------

@pytest.mark.parametrize("secs,expected", [
    (0, "0:00"),
    (59, "0:59"),
    (355, "5:55"),
    (3600, "60:00"),
])
def test_format_duration(secs, expected):
    assert spotify_search.format_duration(secs) == expected


# --- run_search: card rendering ---------------------------------------------------

def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def test_run_search_sends_photo_and_text_cards(monkeypatch):
    tracks = [fake_track(0), fake_track(1, cover_url=None)]
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=tracks)
    )
    update = make_update("starboy")
    context = make_context()

    await search.run_search(update, context, "starboy")

    # candidates stashed for the callback buttons
    assert context.user_data[search.CANDIDATES_KEY] == tracks

    # card 0 has a cover -> photo message
    context.bot.send_photo.assert_awaited_once()
    kw = context.bot.send_photo.await_args.kwargs
    assert kw["photo"] == tracks[0].cover_url
    assert "<b>Title 0</b>" in kw["caption"]
    assert "<i>Artist 0</i>" in kw["caption"]
    assert "📀 Album 0" in kw["caption"]
    assert "3:34" in kw["caption"]
    assert kw["reply_markup"] is not None
    assert _buttons(kw["reply_markup"])[0].callback_data == "song:dl:0"

    # card 1 has no cover -> plain-text fallback, same buttons
    context.bot.send_message.assert_awaited_once()
    kw = context.bot.send_message.await_args.kwargs
    assert "<b>Title 1</b>" in kw["text"]
    assert _buttons(kw["reply_markup"])[0].callback_data == "song:dl:1"


async def test_run_search_escapes_html_in_caption(monkeypatch):
    tracks = [fake_track(0, title="A <b>bold</b> & 'quoted' title", artist=None)]
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=tracks)
    )
    update = make_update("x")
    context = make_context()

    await search.run_search(update, context, "x")

    caption = context.bot.send_photo.await_args.kwargs["caption"]
    assert "A &lt;b&gt;bold&lt;/b&gt; &amp; 'quoted' title" in caption
    assert "<b>bold</b>" not in caption  # no raw user markup survives


async def test_run_search_cover_fetch_failure_falls_back(monkeypatch):
    from telegram.error import BadRequest

    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=[fake_track(0)])
    )
    update = make_update("x")
    context = make_context()
    context.bot.send_photo = AsyncMock(
        side_effect=BadRequest("unable to fetch photo")
    )

    await search.run_search(update, context, "x")  # must not raise

    context.bot.send_message.assert_awaited_once()


async def test_run_search_no_results(monkeypatch):
    monkeypatch.setattr(search, "search_tracks", AsyncMock(return_value=[]))
    update = make_update("zzz qqq")
    context = make_context()

    await search.run_search(update, context, "zzz qqq")

    context.bot.send_photo.assert_not_awaited()
    assert search.CANDIDATES_KEY not in context.user_data
    text = update.message.reply_text.await_args.args[0]
    assert "No results" in text
    assert "zzz qqq" in text


async def test_run_search_backend_failure_reports(monkeypatch):
    monkeypatch.setattr(
        search, "search_tracks",
        AsyncMock(side_effect=RuntimeError("proxy timeout")),
    )
    update = make_update("x")
    context = make_context()

    await search.run_search(update, context, "x")  # must not raise

    text = update.message.reply_text.await_args.args[0]
    assert "Couldn't reach Spotify" in text
    assert search.CANDIDATES_KEY not in context.user_data


async def test_run_search_empty_query_is_noop(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search, "search_tracks", mock)
    update = make_update("   ")
    context = make_context()

    await search.run_search(update, context, "   ")

    mock.assert_not_awaited()


# --- track_keyboard ----------------------------------------------------------------

def test_track_keyboard_buttons():
    kb = keyboards.track_keyboard(2, "https://open.spotify.com/track/xyz")
    rows = kb.inline_keyboard
    assert len(rows) == 2
    assert rows[0][0].text == "⬇️ Download MP3"
    assert rows[0][0].callback_data == "song:dl:2"
    assert rows[1][0].text == "📄 Lyrics"
    assert rows[1][0].callback_data == "song:lyr:2"
    assert rows[1][1].text == "🔗 Open on Spotify"
    assert rows[1][1].url == "https://open.spotify.com/track/xyz"
    assert rows[1][1].callback_data is None


def test_song_action_pattern():
    assert re.match(SONG_ACTION_CALLBACK_PATTERN, "song:dl:0")
    assert re.match(SONG_ACTION_CALLBACK_PATTERN, "song:lyr:12")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "song:dl:x")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "song:prev:0")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "yt:fmt:720")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "nav:music")


# --- on_song_action callbacks -------------------------------------------------------

async def test_song_dl_downloads_candidate(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    tracks = [fake_track(0), fake_track(1)]
    update = make_callback("song:dl:1")
    context = make_context()
    context.user_data[search.CANDIDATES_KEY] = tracks

    await search.on_song_action(update, context)

    mock.assert_awaited_once()
    # the core download core receives (update, context, spotify_url)
    assert mock.await_args.args[2] == tracks[1].spotify_url
    update.callback_query.answer.assert_awaited_once()


async def test_song_lyr_is_coming_soon(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    update = make_callback("song:lyr:0")
    context = make_context()
    context.user_data[search.CANDIDATES_KEY] = [fake_track(0)]

    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    assert update.callback_query.answer.await_args.args[0] == COMMING_SOON


async def test_song_action_stale_index_answers_expired(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    update = make_callback("song:dl:5")
    context = make_context()
    context.user_data[search.CANDIDATES_KEY] = [fake_track(0)]

    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    assert update.callback_query.answer.await_args.args[0] == SONG_EXPIRED


async def test_song_action_without_candidates_answers_expired(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    update = make_callback("song:dl:0")
    context = make_context()  # no candidates at all (restart / never searched)

    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    assert update.callback_query.answer.await_args.args[0] == SONG_EXPIRED


# --- state wiring: plain text -> search ---------------------------------------------

async def test_music_state_handler_searches(monkeypatch):
    """MUSIC_STATE TEXT entry: the typed text becomes the search query."""
    mock = AsyncMock(return_value=[fake_track(0)])
    monkeypatch.setattr(search, "search_tracks", mock)
    update = make_update("  starboy  ")
    context = make_context()

    state = await search.handler(update, context)

    assert state == MUSIC_STATE
    assert mock.await_args.args[0] == "starboy"


async def test_spotify_state_plain_text_searches(monkeypatch):
    """MUSIC_SPOTIFY_STATE: spotify_download.handler's non-URL branch now
    runs the search instead of going silent (plan 1.2)."""
    mock = AsyncMock()
    monkeypatch.setattr(search, "run_search", mock)
    update = make_update("starboy")
    context = make_context()

    state = await spotify_download.handler(update, context)

    assert state == MUSIC_SPOTIFY_STATE
    mock.assert_awaited_once()
    assert mock.await_args.args[2] == "starboy"


async def test_spotify_state_url_still_downloads(monkeypatch):
    dl = AsyncMock()
    monkeypatch.setattr(spotify_download, "run_spotify_download", dl)
    monkeypatch.setattr(search, "run_search", AsyncMock())
    update = make_update("https://open.spotify.com/track/xyz")
    context = make_context()
    context.application.bot_data["spotdl_instance"] = object()

    state = await spotify_download.handler(update, context)

    assert state == MUSIC_SPOTIFY_STATE
    dl.assert_awaited_once()
    assert dl.await_args.args[2] == "https://open.spotify.com/track/xyz"
    search.run_search.assert_not_awaited()
