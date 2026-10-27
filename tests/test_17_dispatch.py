"""#17 universal link dispatcher (plan 1.1) — detection, routing,
YouTube format selector."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.handlers.dispatch as dispatch
import utils.link_detect as link_detect
from utils.constants.keyboards import (
    CB_YT_FMT_360,
    CB_YT_FMT_720,
    CB_YT_FMT_MP3,
)
from utils.constants.states import (
    INSTAGRAM_DOWNLOAD_STATE,
    MUSIC_SPOTIFY_STATE,
    PINTEREST_STATE,
)
from utils import keyboards


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


# --- link_detect ---------------------------------------------------------------

@pytest.mark.parametrize("text,url", [
    ("https://youtu.be/abc123", "https://youtu.be/abc123"),
    ("check this https://www.youtube.com/watch?v=abc123 out", "https://www.youtube.com/watch?v=abc123"),
    ("https://open.spotify.com/track/xyz a https://pinterest.com/pin/1", "https://open.spotify.com/track/xyz"),
])
def test_first_url(text, url):
    assert link_detect.first_url(text) == url


def test_first_url_none():
    assert link_detect.first_url("no link here") is None
    assert link_detect.first_url(None) is None


@pytest.mark.parametrize("url,platform", [
    ("https://open.spotify.com/track/3n3nZbgghv6tn2azLyY6oL", "spotify"),
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x", "spotify"),
    ("https://www.youtube.com/watch?v=abc_-123", "youtube"),
    ("https://youtu.be/abc123", "youtube"),
    ("https://www.youtube.com/shorts/abc123", "youtube"),
    ("https://www.youtube.com/playlist?list=PL123", "youtube"),
    ("https://www.instagram.com/p/Cabc123/", "instagram"),
    ("https://www.instagram.com/reel/Cabc/", "instagram"),
    ("https://www.instagram.com/stories/username/1234567890/", "instagram"),
    ("https://pin.it/abc123", "pinterest"),
    ("https://www.pinterest.com/pin/123456789/", "pinterest"),
    ("https://vm.tiktok.com/ZMabc123/", "tiktok"),
    ("https://www.tiktok.com/@user/video/1234567890", "tiktok"),
])
def test_detect_media(url, platform):
    assert link_detect.detect_media(url) == platform


def test_detect_media_unknown():
    assert link_detect.detect_media("https://www.google.com/search?q=x") is None


def test_media_url_filter():
    f = dispatch.MediaUrlFilter()
    msg = MagicMock()
    msg.text = "https://youtu.be/abc123"
    assert f.filter(msg) is True
    msg.text = "hello"
    assert f.filter(msg) is False
    msg.text = "see https://example.com/page"
    assert f.filter(msg) is False


def test_media_url_filter_check_update_regression():
    """Regression (2026-10-27 12:58 live incident): the framework calls
    the filter's check_update(), not filter(). The filter must subclass
    MessageFilter (which routes check_update -> filter); subclassing
    plain BaseFilter made EVERY message update pass — /start and plain
    text all hit handle_link and crashed on its URL assert."""
    from telegram import Update

    f = dispatch.MediaUrlFilter()

    def msg_update(text):
        return Update.de_json(
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 1, "type": "private"},
                    "text": text,
                },
            },
            None,
        )

    assert not f.check_update(msg_update("/start"))
    assert not f.check_update(msg_update("hello there"))
    assert f.check_update(msg_update("https://youtu.be/abc123"))
    # a callback-only update has no message -> never a media link
    cb_update = Update.de_json(
        {
            "update_id": 2,
            "callback_query": {
                "id": "1",
                "from": {"id": 1, "is_bot": False, "first_name": "a"},
                "chat_instance": "x",
                "data": "d",
            },
        },
        None,
    )
    assert not f.check_update(cb_update)


# --- handle_link routing -------------------------------------------------------

async def test_youtube_link_shows_format_selector():
    update = make_update("https://youtu.be/abc123")
    context = make_context()

    result = await dispatch.handle_link(update, context)

    assert result is None  # state unchanged (or none)
    assert context.user_data["yt_url"] == "https://youtu.be/abc123"
    update.message.reply_text.assert_awaited_once()
    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert markup is keyboards.youtube_format_keyboard


async def test_spotify_link_routes_to_spotify(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dispatch.spotify_download, "run_spotify_download",
        AsyncMock(side_effect=lambda u, c, url: calls.append(url)),
    )
    update = make_update("https://open.spotify.com/track/xyz")
    context = make_context()

    result = await dispatch.handle_link(update, context)

    assert result == MUSIC_SPOTIFY_STATE
    assert calls == ["https://open.spotify.com/track/xyz"]


async def test_instagram_link_routes_to_instagram(monkeypatch):
    monkeypatch.setattr(
        dispatch.instagram_download, "run_instagram_download",
        AsyncMock(),
    )
    update = make_update("https://www.instagram.com/p/abc/")
    context = make_context()

    result = await dispatch.handle_link(update, context)

    assert result == INSTAGRAM_DOWNLOAD_STATE
    dispatch.instagram_download.run_instagram_download.assert_awaited_once()


async def test_pinterest_link_routes_to_pinterest(monkeypatch):
    monkeypatch.setattr(
        dispatch.pinterest_download, "run_pinterest_download",
        AsyncMock(),
    )
    update = make_update("https://pin.it/abc123")
    context = make_context()

    result = await dispatch.handle_link(update, context)

    assert result == PINTEREST_STATE
    dispatch.pinterest_download.run_pinterest_download.assert_awaited_once()


async def test_tiktok_link_routes_to_video_download(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dispatch.youtube_download, "run_video_download",
        AsyncMock(side_effect=lambda u, c, url, **k: calls.append((url, k))),
    )
    update = make_update("https://vm.tiktok.com/ZMabc/")
    context = make_context()

    result = await dispatch.handle_link(update, context)

    assert result is None
    # default quality (720) — the explicit cap arrives with a format click
    assert calls == [("https://vm.tiktok.com/ZMabc/", {})]


# --- YouTube format selector callback -------------------------------------------

async def test_yt_format_without_stored_url_answers_expired():
    update = make_callback(CB_YT_FMT_720)
    context = make_context()

    await dispatch.on_yt_format(update, context)

    update.callback_query.answer.assert_awaited_once()
    assert "expired" in update.callback_query.answer.await_args.args[0].lower()


async def test_yt_format_720_and_360_call_video(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(dispatch.youtube_download, "run_video_download", mock)
    update = make_callback(CB_YT_FMT_360)
    context = make_context()
    context.user_data["yt_url"] = "https://youtu.be/abc123"

    await dispatch.on_yt_format(update, context)

    mock.assert_awaited_once()
    args, kwargs = mock.await_args
    assert args[2] == "https://youtu.be/abc123"
    assert kwargs["max_height"] == 360
    # the stored URL is consumed
    assert "yt_url" not in context.user_data


async def test_yt_format_720(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(dispatch.youtube_download, "run_video_download", mock)
    update = make_callback(CB_YT_FMT_720)
    context = make_context()
    context.user_data["yt_url"] = "https://youtu.be/abc123"

    await dispatch.on_yt_format(update, context)

    _, kwargs = mock.await_args
    assert kwargs["max_height"] == 720


async def test_yt_format_mp3_calls_audio(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(dispatch.youtube_mp3, "run_mp3_download", mock)
    update = make_callback(CB_YT_FMT_MP3)
    context = make_context()
    context.user_data["yt_url"] = "https://youtu.be/abc123"

    await dispatch.on_yt_format(update, context)

    mock.assert_awaited_once()
    assert mock.await_args.args[2] == "https://youtu.be/abc123"
