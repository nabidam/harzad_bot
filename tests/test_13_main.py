"""#13 main.py bootstrap — post_init order, mini app, profile photo, guards.

Real-boot verification is routine (the bot is live); these tests pin the
wiring logic with a mocked bot.
"""
import pytest
from telegram import InputProfilePhotoStatic, MenuButtonWebApp, WebAppInfo
from unittest.mock import AsyncMock, MagicMock

import configurations.settings as settings
import main
from utils.constants.messages import SOMETHING_WENT_WRONG


class RecordingBot:
    def __init__(self):
        self.calls = []
        self.set_my_commands = self._rec("set_my_commands")
        self.set_my_description = self._rec("set_my_description")
        self.set_my_short_description = self._rec("set_my_short_description")
        self.set_my_profile_photo = self._rec("set_my_profile_photo")
        self.set_chat_menu_button = self._rec("set_chat_menu_button")

    def _rec(self, name):
        async def rec(*a, **k):
            self.calls.append(name)
            return MagicMock()

        rec.name = name
        return rec


def make_application(bot):
    app = MagicMock()
    app.bot = bot
    return app


def make_bot():
    bot = MagicMock()
    bot.set_chat_menu_button = AsyncMock()
    bot.set_my_profile_photo = AsyncMock()
    return bot


# --- post_init --------------------------------------------------------------------

async def test_post_init_order(monkeypatch):
    bot = RecordingBot()
    profile = AsyncMock()
    mini = AsyncMock()
    monkeypatch.setattr(main, "set_profile_photo", profile)
    monkeypatch.setattr(main, "start_mini_app", mini)

    await main.post_init(make_application(bot))

    assert bot.calls == [
        "set_my_commands",
        "set_my_description",
        "set_my_short_description",
    ]
    profile.assert_awaited_once_with(bot)
    mini.assert_awaited_once()


# --- start_mini_app ------------------------------------------------------------------

async def test_menu_button_set_when_url_configured(monkeypatch):
    bot = make_bot()
    app = make_application(bot)
    start = AsyncMock()
    monkeypatch.setattr(main, "start_server", start)
    monkeypatch.setattr(settings, "WEB_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "WEB_PORT", 8377)
    monkeypatch.setattr(settings, "MINIAPP_URL", "https://app.example/")

    await main.start_mini_app(app)

    start.assert_awaited_once_with(bot, "127.0.0.1", 8377, "https://app.example/")
    bot.set_chat_menu_button.assert_awaited_once()
    button = bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "Open Harzad"
    assert button.web_app.url == "https://app.example/"


async def test_menu_button_skipped_when_url_empty(monkeypatch):
    bot = make_bot()
    app = make_application(bot)
    start = AsyncMock()
    monkeypatch.setattr(main, "start_server", start)
    monkeypatch.setattr(settings, "WEB_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "WEB_PORT", 8377)
    monkeypatch.setattr(settings, "MINIAPP_URL", "")

    await main.start_mini_app(app)

    # server still starts (loopback, harmless); button not set
    start.assert_awaited_once()
    bot.set_chat_menu_button.assert_not_awaited()


async def test_server_failure_never_blocks_startup(monkeypatch):
    bot = make_bot()
    app = make_application(bot)
    start = AsyncMock(side_effect=OSError("port busy"))
    monkeypatch.setattr(main, "start_server", start)
    monkeypatch.setattr(settings, "MINIAPP_URL", "https://app.example/")

    await main.start_mini_app(app)  # must not raise

    bot.set_chat_menu_button.assert_not_awaited()


async def test_menu_button_failure_never_blocks_startup(monkeypatch):
    bot = RecordingBot()

    async def fail_button(*a, **k):
        raise RuntimeError("api 400")

    bot.set_chat_menu_button = fail_button
    app = make_application(bot)
    monkeypatch.setattr(main, "start_server", AsyncMock())
    monkeypatch.setattr(settings, "MINIAPP_URL", "https://app.example/")

    await main.start_mini_app(app)  # must not raise


# --- set_profile_photo ------------------------------------------------------------------

async def test_profile_photo_from_real_logo():
    bot = MagicMock()
    bot.set_my_profile_photo = AsyncMock()
    await main.set_profile_photo(bot)
    bot.set_my_profile_photo.assert_awaited_once()
    arg = bot.set_my_profile_photo.await_args.args[0]
    assert isinstance(arg, InputProfilePhotoStatic)
    # the attached file bytes are a valid JPEG
    photo = arg.photo
    data = photo.input_file_content
    assert data[:2] == b"\xff\xd8"


async def test_profile_photo_missing_logo_swallowed(tmp_path, monkeypatch):
    bot = MagicMock()
    bot.set_my_profile_photo = AsyncMock()
    monkeypatch.setattr(main, "SCRIPT_DIR", str(tmp_path))
    await main.set_profile_photo(bot)  # must not raise
    bot.set_my_profile_photo.assert_not_awaited()


# --- handle_stray_callback (group 1, no active conversation) ----------------

async def test_stray_callback_answered():
    """A non-nav button click with no conversation is just answered so the
    client's spinner stops."""
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = "not:a:nav:id"
    update.callback_query.answer = AsyncMock()
    await main.handle_stray_callback(update, MagicMock())
    update.callback_query.answer.assert_awaited_once()


async def test_stray_nav_callback_runs_navigator(monkeypatch):
    """A nav button click without a conversation drops the user into the
    menu via the shared navigator (plan 1.1 zero-friction entry)."""
    navigate_mock = AsyncMock()
    monkeypatch.setattr(main, "navigate", navigate_mock)

    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = "nav:start"
    update.callback_query.answer = AsyncMock()
    await main.handle_stray_callback(update, MagicMock())

    navigate_mock.assert_awaited_once()
    update.callback_query.answer.assert_not_awaited()


MSG = {
    "message_id": 1,
    "date": 1,
    "chat": {"id": 1, "type": "private"},
    "text": "x",
}


async def test_stray_callback_no_cq_noop():
    from telegram import Update

    update = Update.de_json({"update_id": 1, "message": MSG}, None)
    assert update.callback_query is None
    await main.handle_stray_callback(update, MagicMock())  # must not raise


# --- on_error -------------------------------------------------------------------------------

async def test_on_error_notifies_user(monkeypatch):
    react = AsyncMock()
    monkeypatch.setattr(main, "react_message", react)

    from telegram import Update

    update = MagicMock(spec=Update)  # isinstance() must pass
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = RuntimeError("boom")

    await main.on_error(update, context)

    react.assert_awaited_once()
    update.effective_message.reply_text.assert_awaited_once()
    # on_error passes the text positionally
    assert update.effective_message.reply_text.await_args.args[0] == SOMETHING_WENT_WRONG


async def test_on_error_without_message_is_quiet(monkeypatch):
    react = AsyncMock()
    monkeypatch.setattr(main, "react_message", react)
    # edited_message-only update: effective_message is None
    from telegram import Update

    # callback-only update without a message: effective_message is None
    update = Update.de_json(
        {
            "update_id": 1,
            "callback_query": {
                "id": "1",
                "from": {"id": 1, "is_bot": False, "first_name": "a"},
                "chat_instance": "x",
                "data": "d",
            },
        },
        None,
    )
    assert update.effective_message is None
    await main.on_error(update, MagicMock())
    react.assert_not_awaited()


async def test_on_error_non_update_object_is_quiet(monkeypatch):
    react = AsyncMock()
    monkeypatch.setattr(main, "react_message", react)
    await main.on_error("not an update", MagicMock())
    react.assert_not_awaited()


# --- register_handlers (plan 1.1 wiring) ---------------------------------------

def test_register_handlers_builds_tree():
    """The real handler tree from main.py must build and carry the
    universal dispatcher in every conversation state (plan 1.1)."""
    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ConversationHandler, MessageHandler
    from utils.constants.states import (
        AI_CHAT_STATE, AI_STATE, INSTAGRAM_DOWNLOAD_STATE, INSTAGRAM_STATE,
        MUSIC_SPOTIFY_STATE, MUSIC_STATE, PINTEREST_STATE, START_STATE,
        YOUTUBE_DOWNLOAD_STATE, YOUTUBE_MP3_STATE, YOUTUBE_STATE,
    )
    from bot.handlers import dispatch
    from bot.handlers.music.spotify import search as spotify_search
    from bot.navigation import navigate

    app = ApplicationBuilder().token(settings.TOKEN).build()
    main.register_handlers(app)

    conv = app.handlers[0][0]
    assert isinstance(conv, ConversationHandler)
    for state in (START_STATE, INSTAGRAM_STATE, INSTAGRAM_DOWNLOAD_STATE,
                  MUSIC_STATE, MUSIC_SPOTIFY_STATE, PINTEREST_STATE,
                  YOUTUBE_STATE, YOUTUBE_DOWNLOAD_STATE, YOUTUBE_MP3_STATE,
                  AI_STATE, AI_CHAT_STATE):
        state_handlers = conv.states[state]
        link_handlers = [
            h for h in state_handlers
            if isinstance(h, MessageHandler) and isinstance(h.filters, dispatch.MediaUrlFilter)
        ]
        assert link_handlers, f"state {state} has no media link handler"
        # the link handler must come before that state's plain TEXT handler
        # (a pasted link must not leak into the state's own input parsing)
        text_handlers = [
            h for h in state_handlers
            if isinstance(h, MessageHandler) and not isinstance(h.filters, dispatch.MediaUrlFilter)
        ]
        if text_handlers:
            assert state_handlers.index(link_handlers[0]) < state_handlers.index(text_handlers[0])
        # pattern-matched callbacks must precede navigate (no pattern):
        # check_update picks the first matching handler per update
        fmt = next(
            h for h in state_handlers
            if isinstance(h, CallbackQueryHandler) and h.callback is dispatch.on_yt_format
        )
        song = next(
            h for h in state_handlers
            if isinstance(h, CallbackQueryHandler) and h.callback is spotify_search.on_song_action
        )
        nav = next(
            h for h in state_handlers
            if isinstance(h, CallbackQueryHandler) and h.callback is navigate
        )
        assert state_handlers.index(fmt) < state_handlers.index(nav)
        assert state_handlers.index(song) < state_handlers.index(nav)

    group1 = app.handlers[1]
    fmt = next(
        h for h in group1
        if isinstance(h, CallbackQueryHandler) and h.callback is dispatch.on_yt_format
    )
    song = next(
        h for h in group1
        if isinstance(h, CallbackQueryHandler) and h.callback is spotify_search.on_song_action
    )
    stray = next(
        h for h in group1
        if isinstance(h, CallbackQueryHandler) and h.callback is main.handle_stray_callback
    )
    # first match in a group consumes the update: the specific handlers
    # must be registered before the catch-all
    assert group1.index(fmt) < group1.index(stray)
    assert group1.index(song) < group1.index(stray)


def test_music_state_searches_plain_text():
    """MUSIC_STATE carries a plain-text search entry (plan 1.2) so typing
    a song name in the music state works without a URL."""
    from telegram.ext import ApplicationBuilder, ConversationHandler, MessageHandler, filters
    from utils.constants.states import MUSIC_STATE
    from bot.handlers.music.spotify import search as spotify_search

    app = ApplicationBuilder().token(settings.TOKEN).build()
    main.register_handlers(app)

    conv = app.handlers[0][0]
    assert isinstance(conv, ConversationHandler)
    # filters.TEXT is a singleton instance — compare by identity
    text_handlers = [
        h for h in conv.states[MUSIC_STATE]
        if isinstance(h, MessageHandler) and h.filters is filters.TEXT
    ]
    assert any(h.callback is spotify_search.handler for h in text_handlers)
