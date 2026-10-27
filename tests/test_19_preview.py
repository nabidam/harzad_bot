"""#19 30s preview resolution (plan 1.4) — iTunes/Deezer fallback,
title matching, and the song:prev card button."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import connectors.preview as preview
import bot.handlers.music.spotify.search as search
import requests
from utils import copy
from utils.tokens import get_token_store


def _fresh_tg_id() -> int:
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


def make_callback(data):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    update.callback_query = cq
    update.effective_chat = MagicMock(id=7)
    update.effective_message = MagicMock(chat_id=7)
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    context.bot = MagicMock()
    for m in ("send_message", "send_chat_action", "send_audio", "send_photo"):
        setattr(context.bot, m, AsyncMock())
    return context


def store_tracks(context, tracks):
    return get_token_store(context, search.RESULTS_STORE).put(
        {"query": "q", "tracks": tracks}, search.RESULTS_TTL_SECONDS
    )


def fake_track(**kw):
    from connectors.spotify_search import TrackResult

    base = dict(
        title="Starboy",
        artist="The Weeknd, Daft Punk",
        album="Starboy",
        duration_sec=230,
        spotify_url="https://open.spotify.com/track/abc",
        cover_url="https://i.scdn.co/image/0.jpg",
        preview_url=None,
    )
    base.update(kw)
    return TrackResult(**base)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


ITUNES_HIT = {
    "results": [
        {"trackName": "Starboy (Remix)", "previewUrl": "https://itunes/1.mp3"},
        {"trackName": "Starboy", "previewUrl": "https://itunes/2.mp3"},
    ]
}
DEEZER_HIT = {
    "data": [
        {"title": "Starboy", "preview": "https://deezer/1.mp3"},
    ]
}


# --- title matching ---------------------------------------------------------------

@pytest.mark.parametrize("title,candidate,expected", [
    ("Starboy", "Starboy (Remix)", True),
    ("Starboy (Remix)", "Starboy", True),
    ("Get Lucky!", "Get Lucky", True),
    ("Starboy", "Starlight", False),
    ("", "Starboy", False),
    ("Starboy", "", False),
])
def test_title_match(title, candidate, expected):
    assert preview._title_match(title, candidate) is expected


# --- fetchers ----------------------------------------------------------------------

def test_itunes_preview_first_matching_result(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _Resp(ITUNES_HIT)

    monkeypatch.setattr(preview.requests, "get", fake_get)

    # top hit is a remix — the plain title still matches it (symmetric
    # containment) and wins
    url = preview._itunes_preview("Starboy", "The Weeknd")

    assert url == "https://itunes/1.mp3"
    (req_url, params) = calls[0]
    assert req_url == "https://itunes.apple.com/search"
    assert params["media"] == "music"
    assert params["term"] == "Starboy The Weeknd"


def test_itunes_preview_skips_mismatched_top_hit(monkeypatch):
    payload = {"results": [
        {"trackName": "Unrelated", "previewUrl": "https://itunes/x.mp3"},
        {"trackName": "Starboy", "previewUrl": "https://itunes/2.mp3"},
    ]}
    monkeypatch.setattr(
        preview.requests, "get", lambda url, params=None, timeout=None: _Resp(payload)
    )
    assert preview._itunes_preview("Starboy", "The Weeknd") == "https://itunes/2.mp3"


def test_itunes_preview_no_match_returns_none(monkeypatch):
    payload = {"results": [{"trackName": "Unrelated", "previewUrl": "u"}]}
    monkeypatch.setattr(
        preview.requests, "get", lambda url, params=None, timeout=None: _Resp(payload)
    )
    assert preview._itunes_preview("Starboy", "The Weeknd") is None


def test_deezer_preview(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _Resp(DEEZER_HIT)

    monkeypatch.setattr(preview.requests, "get", fake_get)

    assert preview._deezer_preview("Starboy", "The Weeknd") == "https://deezer/1.mp3"
    assert calls == ["https://api.deezer.com/search"]


# --- resolve_preview_url_sync: fallback chain ---------------------------------------

def test_resolve_prefers_itunes(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "itunes" in url:
            return _Resp(ITUNES_HIT)
        return _Resp(DEEZER_HIT)

    monkeypatch.setattr(preview.requests, "get", fake_get)

    url = preview.resolve_preview_url_sync("Starboy", "The Weeknd")

    assert url == "https://itunes/1.mp3"
    # iTunes answered; Deezer never queried
    assert calls == ["https://itunes.apple.com/search"]


def test_resolve_falls_back_to_deezer_on_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "itunes" in url:
            raise requests.ConnectionError("proxy timeout")
        return _Resp(DEEZER_HIT)

    monkeypatch.setattr(preview.requests, "get", fake_get)

    url = preview.resolve_preview_url_sync("Starboy", "The Weeknd")

    assert url == "https://deezer/1.mp3"
    assert len(calls) == 2


def test_resolve_falls_back_to_deezer_on_mismatch(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        if "itunes" in url:
            return _Resp({"results": [{"trackName": "Unrelated"}]})
        return _Resp(DEEZER_HIT)

    monkeypatch.setattr(preview.requests, "get", fake_get)

    assert preview.resolve_preview_url_sync("Starboy", "The Weeknd") == "https://deezer/1.mp3"


def test_resolve_none_when_both_miss(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _Resp({"results": [], "data": []})

    monkeypatch.setattr(preview.requests, "get", fake_get)

    assert preview.resolve_preview_url_sync("nope", "nobody") is None


def test_resolve_none_when_both_fail(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(preview.requests, "get", fake_get)

    assert preview.resolve_preview_url_sync("Starboy", "The Weeknd") is None


async def test_resolve_preview_url_async_uses_sync_core(monkeypatch):
    seen = []

    def fake_sync(title, artist):
        seen.append((title, artist))
        return "https://itunes/x.mp3"

    monkeypatch.setattr(preview, "resolve_preview_url_sync", fake_sync)

    url = await preview.resolve_preview_url("Starboy", "The Weeknd")

    assert url == "https://itunes/x.mp3"
    assert seen == [("Starboy", "The Weeknd")]


# --- song:prev card button ------------------------------------------------------------

async def test_prev_uses_track_preview_url_when_present(monkeypatch):
    mock = AsyncMock(return_value="https://should-not-be-used.mp3")
    monkeypatch.setattr(search, "resolve_preview_url", mock)
    track = fake_track(preview_url="https://spotify-preview/x.mp3")
    context = make_context()
    token = store_tracks(context, [track])

    update = make_callback(f"song:prev:{token}:1")
    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    context.bot.send_audio.assert_awaited_once()
    kw = context.bot.send_audio.await_args.kwargs
    assert kw["audio"] == "https://spotify-preview/x.mp3"
    assert kw["title"] == "Starboy"
    assert kw["performer"] == "The Weeknd, Daft Punk"
    update.callback_query.answer.assert_awaited_once()


async def test_prev_inline_token_without_index(monkeypatch):
    """Shared inline card: no index in the payload — the token names the
    single track (spec §5.6)."""
    mock = AsyncMock(return_value="https://itunes/x.mp3")
    monkeypatch.setattr(search, "resolve_preview_url", mock)
    track = fake_track(preview_url=None)
    context = make_context()
    token = store_tracks(context, [track])

    update = make_callback(f"song:prev:{token}")
    await search.on_song_action(update, context)

    mock.assert_awaited_once_with("Starboy", "The Weeknd, Daft Punk")
    kw = context.bot.send_audio.await_args.kwargs
    assert kw["audio"] == "https://itunes/x.mp3"


async def test_prev_resolves_from_itunes_when_spotify_url_missing(monkeypatch):
    mock = AsyncMock(return_value="https://itunes/x.mp3")
    monkeypatch.setattr(search, "resolve_preview_url", mock)
    track = fake_track(preview_url=None)
    context = make_context()
    token = store_tracks(context, [track])

    update = make_callback(f"song:prev:{token}:1")
    await search.on_song_action(update, context)

    mock.assert_awaited_once_with("Starboy", "The Weeknd, Daft Punk")
    kw = context.bot.send_audio.await_args.kwargs
    assert kw["audio"] == "https://itunes/x.mp3"


async def test_prev_unavailable_answers_no_preview(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(search, "resolve_preview_url", mock)
    track = fake_track(preview_url=None)
    context = make_context()
    token = store_tracks(context, [track])

    update = make_callback(f"song:prev:{token}:1")
    await search.on_song_action(update, context)

    context.bot.send_audio.assert_not_awaited()
    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TRACK_NO_PREVIEW
    )


@pytest.mark.live
def test_live_itunes_or_deezer_resolves_starboy():
    """Real-network check (excluded from the default run): the public
    catalog fallback must produce a 30s MP3 URL for a known track."""
    url = preview.resolve_preview_url_sync("Starboy", "The Weeknd")
    assert url is not None
    assert ".mp3" in url
