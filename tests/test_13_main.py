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


# --- answer_stray_callback ----------------------------------------------------------------

async def test_stray_callback_answered():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    await main.answer_stray_callback(update, MagicMock())
    update.callback_query.answer.assert_awaited_once()


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
    await main.answer_stray_callback(update, MagicMock())  # must not raise


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
