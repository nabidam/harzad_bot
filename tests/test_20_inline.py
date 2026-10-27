"""#20 inline query mode (redesign §5.6) — @harzad_bot <song> articles,
group-safe track tokens, help/failure cards, wiring."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.handlers.inline as inline
import connectors.spotify_search as spotify_search
from telegram import InputTextMessageContent
from utils import copy
from utils.tokens import get_token_store


def _fresh_tg_id() -> int:
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


def make_inline_update(query):
    update = MagicMock()
    iq = MagicMock()
    iq.query = query
    iq.answer = AsyncMock()
    update.inline_query = iq
    update.effective_user = MagicMock(
        id=_fresh_tg_id(), first_name="F", last_name="L", username="u"
    )
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    return context


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


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def test_inline_query_answers_articles(monkeypatch):
    tracks = [fake_track(i) for i in range(3)]
    monkeypatch.setattr(inline, "search_tracks", AsyncMock(return_value=tracks))
    update = make_inline_update("starboy")
    context = make_context()

    await inline.handler(update, context)

    update.inline_query.answer.assert_awaited_once()
    args, kwargs = update.inline_query.answer.await_args
    results = args[0]
    assert len(results) == 3
    # results cached 300s at the API level
    assert kwargs["cache_time"] == spotify_search.CACHE_TTL_SECONDS == 300

    first = results[0]
    # the article id is the track's own token
    assert first.id == results[0].reply_markup.inline_keyboard[0][0].callback_data.split(":")[2]
    assert first.title == "Title 0"
    assert "Artist 0" in first.description
    assert "Album 0" in first.description
    assert first.thumbnail_url == tracks[0].cover_url
    content = first.input_message_content
    assert isinstance(content, InputTextMessageContent)
    assert "<b>Title 0</b>" in content.message_text
    assert content.parse_mode == "HTML"

    # group-safe card buttons (spec §5.6): token only, no index,
    # "Send MP3 here" label, no carousel row
    buttons = _buttons(first.reply_markup)
    assert buttons[0].callback_data == f"song:dl:{first.id}"
    assert buttons[0].text == copy.BTN_TRACK_DOWNLOAD_INLINE
    assert buttons[1].callback_data == f"song:prev:{first.id}"
    flat = [b.callback_data for b in buttons if b.callback_data]
    assert not any("prevcard" in cb or "next" in cb for cb in flat)

    # each article carries its own immutable single-track token in the
    # shared store: the delivered card resolves for ANY user who taps it
    store = get_token_store(context, inline.spotify_search.RESULTS_STORE)
    entry = store.get(first.id)
    assert entry is not None
    assert entry["tracks"] == [tracks[0]]
    assert len(store) == 3  # one token per article


async def test_inline_query_short_query_answers_help_card(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(inline, "search_tracks", mock)
    update = make_inline_update("s")
    context = make_context()

    await inline.handler(update, context)

    mock.assert_not_awaited()
    results = update.inline_query.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].title == copy.INLINE_MIN_QUERY_TITLE
    assert results[0].description == copy.INLINE_MIN_QUERY_DESC
    assert results[0].reply_markup is None


@pytest.mark.parametrize("query", ["", "   "])
async def test_inline_query_empty_query_answers_help_card(monkeypatch, query):
    mock = AsyncMock()
    monkeypatch.setattr(inline, "search_tracks", mock)
    update = make_inline_update(query)
    context = make_context()

    await inline.handler(update, context)

    mock.assert_not_awaited()
    results = update.inline_query.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].title == copy.INLINE_MIN_QUERY_TITLE


async def test_inline_query_no_results_answers_explanation_card(monkeypatch):
    monkeypatch.setattr(inline, "search_tracks", AsyncMock(return_value=[]))
    update = make_inline_update("zzz qqq")
    context = make_context()

    await inline.handler(update, context)

    results = update.inline_query.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].title == "No matches"
    assert "zzz qqq" in results[0].input_message_content.message_text


async def test_inline_query_search_failure_answers_error_card(monkeypatch):
    monkeypatch.setattr(
        inline, "search_tracks", AsyncMock(side_effect=RuntimeError("proxy"))
    )
    update = make_inline_update("starboy")

    await inline.handler(update, make_context())  # must not raise

    results = update.inline_query.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].title == "Search failed"
    assert copy.SEARCH_FAILED in results[0].input_message_content.message_text


def test_article_fields_are_capped():
    track = fake_track(
        0,
        title="T" * 200,
        artist="A" * 300,
        album="B" * 300,
    )
    article = inline._to_article(track, "ab12cd34")
    assert len(article.title) == 64
    assert len(article.description) == 256


def test_article_description_omits_empty_album():
    track = fake_track(0, album="")
    article = inline._to_article(track, "ab12cd34")
    assert article.description == "Artist 0"
    assert "•" not in article.description
