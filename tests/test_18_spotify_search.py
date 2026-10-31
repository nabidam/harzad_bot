"""#18 Spotify text search & the single editable result card (redesign §5) —
raw search mapping, 300s cache, card rendering, tokenized song-card
callbacks, carousel, stale toasts, state wiring, live search feedback +
cancel + the §4.4 timeout guard."""
import asyncio
import re
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import connectors.spotify_search as spotify_search
import bot.handlers.music.spotify.download as spotify_download
import bot.handlers.music.spotify.search as search
import spotdl.utils.spotify as spotdl_spotify
from telegram.error import BadRequest
from utils import copy, keyboards
from utils.constants.keyboards import (
    SONG_ACTION_CALLBACK_PATTERN,
    SONG_ACTION_LEGACY_PATTERN,
)
from utils.constants.states import SEARCH_INPUT_STATE
from utils.tokens import get_token_store


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
    # The search status message: live_status() sends it via send_message
    # and the caller deletes it when the search is done.
    status = MagicMock()
    status.message_id = 500
    status.delete = AsyncMock()
    context.status_message = status
    context.bot = MagicMock()
    for m in ("send_chat_action", "send_video", "send_audio",
              "send_photo", "editMessageText", "edit_message_text",
              "edit_message_media", "deleteMessage", "delete_message",
              "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    context.bot.send_message = AsyncMock(return_value=status)
    return context


def make_callback(data, *, message=None):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    cq.message = message or MagicMock(chat_id=7, message_id=600, photo=None)
    cq.message_id = None
    update.callback_query = cq
    update.effective_message = cq.message
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


def store_tracks(context, tracks, query="q", ttl=search.RESULTS_TTL_SECONDS):
    """Put a result set in the shared token store; return its token."""
    return get_token_store(context, search.RESULTS_STORE).put(
        {"query": query, "tracks": tracks}, ttl
    )


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


# --- run_search: the single result card (spec §5.3) --------------------------------

def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _card_calls(context, marker):
    """send_message is also the live-status channel — pick the card calls."""
    return [
        c for c in context.bot.send_message.await_args_list
        if marker in c.kwargs.get("text", "")
    ]


async def test_run_search_sends_one_card_with_token_buttons(monkeypatch):
    tracks = [fake_track(0), fake_track(1)]
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=tracks)
    )
    update = make_update("starboy")
    context = make_context()

    await search.run_search(update, context, "starboy")

    # ONE card, not three messages (spec §5.3); the first match is shown
    context.bot.send_photo.assert_awaited_once()
    kw = context.bot.send_photo.await_args.kwargs
    assert kw["photo"] == tracks[0].cover_url
    assert "Match 1 of 2" in kw["caption"]
    assert kw["protect_content"] is False
    flat = [b.callback_data for b in _buttons(kw["reply_markup"]) if b.callback_data]
    token = flat[0].split(":")[2]
    assert f"song:dl:{token}:1" in flat
    assert f"song:prev:{token}:1" in flat
    assert f"song:prevcard:{token}:1" in flat
    assert f"song:next:{token}:1" in flat
    assert f"search:again:{token}" in flat
    assert "nav:home" in flat
    # the result set is cached behind that token for the card's lifetime
    entry = get_token_store(context, search.RESULTS_STORE).get(token)
    assert entry is not None
    assert entry["tracks"] == tracks
    assert entry["query"] == "starboy"

    # the ephemeral status message was cleaned up
    context.status_message.delete.assert_awaited_once()


async def test_run_search_first_match_without_cover_is_text_card(monkeypatch):
    tracks = [fake_track(0, cover_url=None), fake_track(1)]
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=tracks)
    )
    update = make_update("x")
    context = make_context()

    await search.run_search(update, context, "x")

    context.bot.send_photo.assert_not_awaited()
    card_calls = _card_calls(context, "<b>Title 0</b>")
    assert len(card_calls) == 1
    kw = card_calls[0].kwargs
    flat = [b.callback_data for b in _buttons(kw["reply_markup"]) if b.callback_data]
    assert f"song:dl:{flat[0].split(':')[2]}:1" in flat


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
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=[fake_track(0)])
    )
    update = make_update("x")
    context = make_context()
    context.bot.send_photo = AsyncMock(
        side_effect=BadRequest("unable to fetch photo")
    )

    await search.run_search(update, context, "x")  # must not raise

    assert len(_card_calls(context, "<b>Title 0</b>")) == 1


async def test_run_search_no_results(monkeypatch):
    monkeypatch.setattr(search, "search_tracks", AsyncMock(return_value=[]))
    update = make_update("zzz qqq")
    context = make_context()

    await search.run_search(update, context, "zzz qqq")

    context.bot.send_photo.assert_not_awaited()
    text = update.message.reply_text.await_args.args[0]
    assert "No matches" in text
    assert "zzz qqq" in text
    context.status_message.delete.assert_awaited_once()


async def test_run_search_backend_failure_reports(monkeypatch):
    monkeypatch.setattr(
        search, "search_tracks",
        AsyncMock(side_effect=RuntimeError("proxy timeout")),
    )
    update = make_update("x")
    context = make_context()

    await search.run_search(update, context, "x")  # must not raise

    text = update.message.reply_text.await_args.args[0]
    assert text == copy.SEARCH_FAILED
    context.status_message.delete.assert_awaited_once()


async def test_run_search_empty_query_is_noop(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search, "search_tracks", mock)
    update = make_update("   ")
    context = make_context()

    await search.run_search(update, context, "   ")

    mock.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()  # no status without a query


# --- live search feedback + cancel ---------------------------------------------------

async def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


def test_search_cancel_keyboard():
    """Spec §4.2: Cancel for every long operation, Tasks when useful —
    both on the live search status."""
    kb = keyboards.search_cancel_keyboard("ab12cd34")
    row = kb.inline_keyboard[0]
    assert [b.text for b in row] == [copy.BTN_SEARCH_CANCEL, copy.BTN_TASKS]
    assert row[0].callback_data == "search:cancel:ab12cd34"
    assert row[1].callback_data == "nav:tasks"
    for b in row:
        assert b.style is None  # neutral — escape hatches, not the action


def test_search_timeout_keyboard():
    """Spec §5.4: the timeout result carries [Search again] [Home]."""
    kb = keyboards.search_timeout_keyboard("ab12cd34")
    row = kb.inline_keyboard[0]
    assert [b.text for b in row] == [copy.BTN_SEARCH_AGAIN, copy.BTN_HOME]
    assert row[0].callback_data == "search:again:ab12cd34"
    assert row[1].callback_data == "nav:home"


def test_search_timeout_guard_is_90s():
    """Spec §4.4 proposed guard (owner decision: default applies)."""
    assert search.SEARCH_TIMEOUT_SECONDS == 90


def test_search_cancel_pattern():
    from utils.constants.keyboards import SEARCH_CANCEL_CALLBACK_PATTERN

    assert re.match(SEARCH_CANCEL_CALLBACK_PATTERN, "search:cancel:ab12cd34")
    # the pre-token payload stays accepted as a legacy alias
    assert re.match(SEARCH_CANCEL_CALLBACK_PATTERN, "srx:c:ab12cd34")
    assert not re.match(SEARCH_CANCEL_CALLBACK_PATTERN, "search:cancel:xyz")
    assert not re.match(SEARCH_CANCEL_CALLBACK_PATTERN, "song:dl:0")


async def test_run_search_status_up_before_result(monkeypatch):
    """The moment the query is typed, a status message with a Cancel
    button is sent — feedback must not wait for the (slow) search."""
    release = asyncio.Event()

    async def slow_search(term):
        await release.wait()
        return [fake_track(0)]

    monkeypatch.setattr(search, "search_tracks", slow_search)
    update = make_update("starboy")
    context = make_context()

    task = asyncio.create_task(search.run_search(update, context, "starboy"))
    await _wait_for(
        lambda: search.CANCEL_KEY in context.user_data
        and context.bot.send_message.await_count >= 1
    )

    # status message is already up, carrying the live Cancel button
    status_kw = context.bot.send_message.await_args_list[0].kwargs
    assert "starboy" in status_kw["text"]
    seq, _event = context.user_data[search.CANCEL_KEY]
    assert (
        status_kw["reply_markup"].inline_keyboard[0][0].callback_data
        == f"search:cancel:{seq}"
    )

    release.set()
    await task

    context.bot.send_photo.assert_awaited_once()
    context.status_message.delete.assert_awaited_once()


async def test_run_search_cancelled(monkeypatch):
    """Cancelling mid-search stops the wait: no card, a clean
    'cancelled' message, status removed."""
    release = asyncio.Event()

    async def slow_search(term):
        await release.wait()
        return [fake_track(0)]

    monkeypatch.setattr(search, "search_tracks", slow_search)
    update = make_update("starboy")
    context = make_context()

    task = asyncio.create_task(search.run_search(update, context, "starboy"))
    await _wait_for(lambda: search.CANCEL_KEY in context.user_data)
    seq, _event = context.user_data[search.CANCEL_KEY]

    # the user taps Cancel on the status message
    await search.on_search_cancel(make_callback(f"search:cancel:{seq}"), context)

    # the cancel wins the race — the search is still pending here
    await task
    release.set()  # release the (already cancelled) search's wait — hygiene

    context.bot.send_photo.assert_not_awaited()
    # a cancelled search stores no result set
    assert len(get_token_store(context, search.RESULTS_STORE)) == 0
    text = update.message.reply_text.await_args.args[0]
    assert "cancelled" in text.lower()
    context.status_message.delete.assert_awaited_once()


async def test_run_search_times_out_with_retry_card(monkeypatch):
    """Spec §4.4/§5.4: a search that has not answered by the guard ends
    in the timeout card — [Search again] [Home], no result card, status
    removed. The late backend result is discarded, never sent."""
    monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.1)

    async def slow_search(term):
        await asyncio.sleep(0.4)
        return [fake_track(0)]

    monkeypatch.setattr(search, "search_tracks", slow_search)
    update = make_update("starboy")
    context = make_context()

    await search.run_search(update, context, "starboy")

    context.bot.send_photo.assert_not_awaited()  # no result card
    context.status_message.delete.assert_awaited_once()

    kw = update.message.reply_text.await_args
    assert kw.args[0] == copy.SEARCH_TIMEOUT
    row = kw.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.text for b in row] == [copy.BTN_SEARCH_AGAIN, copy.BTN_HOME]
    token = row[0].callback_data.removeprefix("search:again:")

    # the retry token names the timed-out query (empty result set) so
    # search:again can re-run it
    entry = get_token_store(context, search.RESULTS_STORE).get(token)
    assert entry["query"] == "starboy"
    assert entry["tracks"] == []


async def test_search_finishing_in_grace_is_honoured(monkeypatch):
    """A search that finishes during the post-timeout grace (a process
    stop across the guard line: the local timer expired while the
    worker had already returned) still delivers the card — finished
    work is never discarded at the guard line (incident 2026-10-28
    21:39)."""
    monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.5)

    async def late_search(term):
        # answers at ~0.55 s: after the 0.5 s guard, inside the 0.1 s
        # grace
        await asyncio.sleep(0.55)
        return [fake_track(0)]

    monkeypatch.setattr(search, "search_tracks", late_search)
    update = make_update("starboy")
    context = make_context()

    await search.run_search(update, context, "starboy")

    context.bot.send_photo.assert_awaited_once()  # the card went out
    context.status_message.delete.assert_awaited_once()


async def test_search_timeout_retry_reruns_the_query(monkeypatch):
    """[Search again] on the timeout card re-runs the same query — the
    search:again path, so the 300 s stale rule applies to it too."""
    monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.1)

    async def slow_search(term):
        await asyncio.sleep(0.4)
        return [fake_track(0)]

    monkeypatch.setattr(search, "search_tracks", slow_search)
    update = make_update("starboy")
    context = make_context()
    await search.run_search(update, context, "starboy")

    token = (
        update.message.reply_text.await_args.kwargs["reply_markup"]
        .inline_keyboard[0][0].callback_data.removeprefix("search:again:")
    )

    # the retry finds the backend fast
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=[fake_track(0)])
    )
    await search.on_search_again(make_callback(f"search:again:{token}"), context)

    context.bot.send_photo.assert_awaited_once()


async def test_finished_search_wins_over_the_guard(monkeypatch):
    """A search that lands just before the guard still delivers its
    card — the guard must never discard finished work."""
    monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.2)

    async def just_in_time(term):
        await asyncio.sleep(0.05)
        return [fake_track(0)]

    monkeypatch.setattr(search, "search_tracks", just_in_time)
    update = make_update("starboy")
    context = make_context()

    await search.run_search(update, context, "starboy")

    context.bot.send_photo.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()


async def test_on_search_cancel_stale_seq_toast(monkeypatch):
    """A Cancel button from a replaced search must not cancel the newer
    one — it only toasts."""
    monkeypatch.setattr(
        search, "search_tracks", AsyncMock(return_value=[fake_track(0)])
    )
    update = make_update("starboy")
    context = make_context()

    await search.run_search(update, context, "starboy")
    _seq, event = context.user_data[search.CANCEL_KEY]
    assert not event.is_set()

    stale = make_callback("search:cancel:ffffffff")
    await search.on_search_cancel(stale, context)

    assert not event.is_set()  # the live search is untouched
    assert (
        stale.callback_query.answer.await_args.args[0]
        == copy.TOAST_SEARCH_CANCEL_STALE
    )


async def test_on_search_cancel_fresh_seq_sets_event():
    event = asyncio.Event()
    context = make_context()
    context.user_data[search.CANCEL_KEY] = ("ab12cd34", event)

    cb_update = make_callback("search:cancel:ab12cd34")
    await search.on_search_cancel(cb_update, context)

    assert event.is_set()
    # plain answer, no alert
    assert cb_update.callback_query.answer.await_args.args == ()


# --- track card payload patterns ------------------------------------------------------

def test_song_action_pattern():
    # tokenized payloads: 8-hex token, optional 1-based index
    assert re.match(SONG_ACTION_CALLBACK_PATTERN, "song:dl:ab12cd34:1")
    assert re.match(SONG_ACTION_CALLBACK_PATTERN, "song:prev:ab12cd34")
    assert re.match(SONG_ACTION_CALLBACK_PATTERN, "song:prevcard:ab12cd34:2")
    assert re.match(SONG_ACTION_CALLBACK_PATTERN, "song:next:ab12cd34:3")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "song:dl:0")  # legacy
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "song:lyr:12")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "yt:fmt:720")
    assert not re.match(SONG_ACTION_CALLBACK_PATTERN, "nav:music")
    # legacy payloads match only the legacy pattern
    assert re.match(SONG_ACTION_LEGACY_PATTERN, "song:dl:0")
    assert re.match(SONG_ACTION_LEGACY_PATTERN, "song:lyr:12")
    assert not re.match(SONG_ACTION_LEGACY_PATTERN, "song:dl:ab12cd34:1")


# --- on_song_action callbacks (spec §5.3, §5.6) ---------------------------------------

async def test_song_dl_downloads_the_named_match(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    tracks = [fake_track(0), fake_track(1)]
    context = make_context()
    token = store_tracks(context, tracks)

    update = make_callback(f"song:dl:{token}:2")
    await search.on_song_action(update, context)

    mock.assert_awaited_once()
    # the download core receives (update, context, spotify_url)
    assert mock.await_args.args[2] == tracks[1].spotify_url
    update.callback_query.answer.assert_awaited_once()


async def test_song_dl_inline_token_without_index(monkeypatch):
    """Shared inline card: the token names the single track itself, so
    any user may tap it in any chat (spec §5.6)."""
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    track = fake_track(7)
    context = make_context()
    token = store_tracks(context, [track])

    await search.on_song_action(make_callback(f"song:dl:{token}"), context)

    mock.assert_awaited_once()
    assert mock.await_args.args[2] == track.spotify_url


async def test_song_dl_shared_card_runs_for_the_tapping_member(monkeypatch):
    """Group safety (spec §11.2): the shared card's token lives in the
    process-wide store, so a DIFFERENT member in a DIFFERENT chat can tap
    Download and the action runs for the tapper — never for the searcher,
    and never against the searcher's private state."""
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    track = fake_track(7)
    context = make_context()
    token = store_tracks(context, [track])

    tapper = make_callback(
        f"song:dl:{token}",
        message=MagicMock(chat_id=99, message_id=601, photo=None),
    )
    tapper.effective_user = MagicMock(id=4_242_424_242, first_name="M")
    await search.on_song_action(tapper, context)

    mock.assert_awaited_once()
    # the download core receives the TAPPER's update (runs for the clicker)
    assert mock.await_args.args[0] is tapper
    assert mock.await_args.args[2] == track.spotify_url
    assert tapper.callback_query.answer.await_args.args == ()  # plain ack


async def test_song_action_stale_token_toasts_and_runs_nothing(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    context = make_context()  # nothing in the store (restart / expired)

    update = make_callback("song:dl:deadbeef:1")
    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_SEARCH_STALE
    )


async def test_song_action_out_of_range_index_toasts(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    context = make_context()
    token = store_tracks(context, [fake_track(0)])

    update = make_callback(f"song:dl:{token}:9")
    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_SEARCH_STALE
    )


async def test_song_action_expired_token_toasts(monkeypatch):
    """TTL passed (300 s): the token resolves to None -> stale toast,
    never the user's latest results (spec §5.3, §16)."""
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    context = make_context()
    token = store_tracks(context, [fake_track(0)], ttl=-1)  # already expired

    update = make_callback(f"song:dl:{token}:1")
    await search.on_song_action(update, context)

    mock.assert_not_awaited()
    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_SEARCH_STALE
    )


async def test_legacy_song_payload_toasts(monkeypatch):
    """Pre-token rollout buttons (index only) are unresolvable by design:
    toast, never the most-recent-results fallback (spec §16)."""
    mock = AsyncMock()
    monkeypatch.setattr(search.spotify_download, "run_spotify_download", mock)
    context = make_context()
    # even with a fresh result set in the store, the legacy payload
    # cannot be tied to it
    store_tracks(context, [fake_track(0)], query="fresh")

    for payload in ("song:dl:0", "song:lyr:1", "song:prev:2"):
        update = make_callback(payload)
        await search.on_song_action_legacy(update, context)
        assert (
            update.callback_query.answer.await_args.args[0]
            == copy.TOAST_SEARCH_STALE
        )
    mock.assert_not_awaited()


# --- carousel: prevcard/next edit the one card (spec §5.3) ---------------------------

async def test_song_next_edits_card_to_next_match():
    tracks = [fake_track(0), fake_track(1)]
    context = make_context()
    token = store_tracks(context, tracks)
    card_message = MagicMock(chat_id=7, message_id=600, photo=[MagicMock()])

    update = make_callback(f"song:next:{token}:1", message=card_message)
    await search.on_song_action(update, context)

    # edited in place — no new message, no toast
    context.bot.send_message.assert_not_awaited()
    update.callback_query.answer.assert_not_awaited()
    mm = context.bot.edit_message_media
    mm.assert_awaited_once()
    kw = mm.await_args.kwargs
    assert kw["media"].media == tracks[1].cover_url
    assert "<b>Title 1</b>" in kw["media"].caption
    assert "Match 2 of 2" in kw["media"].caption
    flat = [b.callback_data for b in _buttons(kw["reply_markup"]) if b.callback_data]
    assert f"song:dl:{token}:2" in flat
    assert f"song:next:{token}:2" in flat


async def test_song_prevcard_at_first_toasts():
    context = make_context()
    token = store_tracks(context, [fake_track(0), fake_track(1)])

    update = make_callback(f"song:prevcard:{token}:1")
    await search.on_song_action(update, context)

    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TRACK_FIRST
    )
    context.bot.edit_message_media.assert_not_awaited()
    context.bot.edit_message_text.assert_not_awaited()


async def test_song_next_at_last_toasts():
    context = make_context()
    token = store_tracks(context, [fake_track(0), fake_track(1)])

    update = make_callback(f"song:next:{token}:2")
    await search.on_song_action(update, context)

    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TRACK_LAST
    )
    context.bot.edit_message_media.assert_not_awaited()


async def test_song_next_to_match_without_cover_edits_text():
    """Next match has no cover: the same message is edited to the text
    card (spec §5.3 fallback)."""
    tracks = [fake_track(0), fake_track(1, cover_url=None)]
    context = make_context()
    token = store_tracks(context, tracks)

    await search.on_song_action(make_callback(f"song:next:{token}:1"), context)

    context.bot.edit_message_media.assert_not_awaited()
    kw = context.bot.edit_message_text.await_args.kwargs
    assert "<b>Title 1</b>" in kw["text"]
    assert "Match 2 of 2" in kw["text"]


async def test_song_next_media_edit_failure_falls_back_to_text():
    tracks = [fake_track(0), fake_track(1)]
    context = make_context()
    token = store_tracks(context, tracks)
    context.bot.edit_message_media = AsyncMock(
        side_effect=BadRequest("message can't be edited")
    )

    await search.on_song_action(make_callback(f"song:next:{token}:1"), context)

    kw = context.bot.edit_message_text.await_args.kwargs
    assert "<b>Title 1</b>" in kw["text"]


# --- search again ----------------------------------------------------------------------

async def test_search_again_reruns_the_same_query(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(search, "run_search", run)
    context = make_context()
    token = store_tracks(context, [fake_track(0)], query="Daft Punk Get Lucky")

    update = make_callback(f"search:again:{token}")
    await search.on_search_again(update, context)

    run.assert_awaited_once()
    assert run.await_args.args[2] == "Daft Punk Get Lucky"
    # plain answer: the new search sends its own status immediately
    assert update.callback_query.answer.await_args.args == ()


async def test_search_again_stale_token_toasts(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(search, "run_search", run)
    context = make_context()

    update = make_callback("search:again:deadbeef")
    await search.on_search_again(update, context)

    run.assert_not_awaited()
    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_SEARCH_STALE
    )


# --- state wiring: plain text -> search ---------------------------------------------

async def test_search_input_handler_searches(monkeypatch):
    """SEARCH_INPUT_STATE TEXT entry: the typed text becomes the query."""
    mock = AsyncMock(return_value=[fake_track(0)])
    monkeypatch.setattr(search, "search_tracks", mock)
    update = make_update("  starboy  ")
    context = make_context()

    state = await search.handler(update, context)

    assert state == SEARCH_INPUT_STATE
    assert mock.await_args.args[0] == "starboy"


async def test_search_input_plain_text_searches(monkeypatch):
    """The shared Search Music entry (spotify_download.handler): non-URL
    text runs the search."""
    mock = AsyncMock()
    monkeypatch.setattr(search, "run_search", mock)
    update = make_update("starboy")
    context = make_context()

    state = await spotify_download.handler(update, context)

    assert state == SEARCH_INPUT_STATE
    mock.assert_awaited_once()
    assert mock.await_args.args[2] == "starboy"


async def test_search_input_url_still_downloads(monkeypatch):
    dl = AsyncMock()
    monkeypatch.setattr(spotify_download, "run_spotify_download", dl)
    monkeypatch.setattr(search, "run_search", AsyncMock())
    update = make_update("https://open.spotify.com/track/xyz")
    context = make_context()
    context.application.bot_data["spotdl_instance"] = object()

    state = await spotify_download.handler(update, context)

    assert state == SEARCH_INPUT_STATE
    dl.assert_awaited_once()
    assert dl.await_args.args[2] == "https://open.spotify.com/track/xyz"
    search.run_search.assert_not_awaited()
