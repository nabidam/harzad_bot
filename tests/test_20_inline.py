"""#20 inline query mode (plan 1.3) — @harzad_bot <song> articles,
caching, candidate stashing, wiring."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.handlers.inline as inline
import connectors.spotify_search as spotify_search
from telegram import InputTextMessageContent
from utils import keyboards


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
    # plan 5.3: results cached 300s
    assert kwargs["cache_time"] == spotify_search.CACHE_TTL_SECONDS == 300

    first = results[0]
    assert first.id == "0"
    assert first.title == "Title 0"
    assert "Artist 0" in first.description
    assert "Album 0" in first.description
    assert first.thumbnail_url == tracks[0].cover_url
    content = first.input_message_content
    assert isinstance(content, InputTextMessageContent)
    assert "<b>Title 0</b>" in content.message_text
    assert content.parse_mode == "HTML"
    # same card buttons as the in-chat search (plan 1.2)
    buttons = [b for row in first.reply_markup.inline_keyboard for b in row]
    assert buttons[0].callback_data == "song:dl:0"
    assert buttons[1].callback_data == "song:prev:0"
    # candidates stashed so the delivered card's buttons resolve
    assert context.user_data[inline.CANDIDATES_KEY] == tracks


async def test_inline_query_short_query_answers_empty(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(inline, "search_tracks", mock)
    update = make_inline_update("s")
    context = make_context()

    await inline.handler(update, context)

    mock.assert_not_awaited()
    assert update.inline_query.answer.await_args.args[0] == []


async def test_inline_query_empty_query_answers_empty(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(inline, "search_tracks", mock)
    for query in ("", "   "):
        update = make_inline_update(query)
        await inline.handler(update, make_context())
        assert update.inline_query.answer.await_args.args[0] == []
    mock.assert_not_awaited()


async def test_inline_query_no_results_answers_empty(monkeypatch):
    monkeypatch.setattr(inline, "search_tracks", AsyncMock(return_value=[]))
    update = make_inline_update("zzz qqq")
    context = make_context()

    await inline.handler(update, context)

    assert update.inline_query.answer.await_args.args[0] == []
    assert inline.CANDIDATES_KEY not in context.user_data


async def test_inline_query_search_failure_answers_empty(monkeypatch):
    monkeypatch.setattr(
        inline, "search_tracks", AsyncMock(side_effect=RuntimeError("proxy"))
    )
    update = make_inline_update("starboy")

    await inline.handler(update, make_context())  # must not raise

    assert update.inline_query.answer.await_args.args[0] == []


def test_article_fields_are_capped():
    track = fake_track(
        0,
        title="T" * 200,
        artist="A" * 300,
        album="B" * 300,
    )
    article = inline._to_article(0, track)
    assert len(article.title) == 64
    assert len(article.description) == 256


def test_article_description_omits_empty_album():
    track = fake_track(0, album="")
    article = inline._to_article(0, track)
    assert article.description == "Artist 0"
    assert "•" not in article.description
