"""#12 chat handlers — legacy handler-level tests with mocked bot.

The YouTube download-flow tests moved to tests/test_22_youtube_flow.py
(redesign §6.3). The decorators (sync_user/log_message) run against the
live test DB with unique tg_ids, cleaned up per test.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.db import SessionLocal
from models.log import Log
from models.user import User
from utils import decorators
from utils.constants.states import HOME_STATE, SEARCH_INPUT_STATE


def _fresh_tg_id() -> int:
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


@pytest.fixture
async def test_tg():
    tg_id = _fresh_tg_id()
    yield tg_id
    # the sync_user/log_message DB work is background (spec §4.1): wait
    # for it before deleting the test user's rows, or a late write
    # recreates them after the cleanup
    await decorators.join_pending_db_work()
    db = SessionLocal()
    db.query(Log).filter(Log.tg_id == tg_id).delete()
    db.query(User).filter(User.tg_id == tg_id).delete()
    db.commit()
    db.close()


def make_update(text, tg_id, chat_id=1):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.chat_id = chat_id
    update.message.message_id = 7
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock(id=tg_id, first_name="F", last_name="L", username="u")
    return update


def make_context():
    context = MagicMock()
    # search status message: live_status() sends it via send_message and
    # the search flow deletes it when done
    status = MagicMock()
    status.message_id = 500
    status.delete = AsyncMock()
    context.status_message = status
    context.bot = MagicMock()
    for m in (
        "send_chat_action", "send_video", "send_audio",
        "send_photo", "editMessageText", "deleteMessage", "set_message_reaction",
    ):
        setattr(context.bot, m, AsyncMock())
    context.bot.send_message = AsyncMock(return_value=status)
    return context




# --- Pinterest ----------------------------------------------------------------------
# The legacy Pinterest handler tests lived here; the handler body was
# removed in step 8 (the input screen is bot/handlers/pinterest/main.py,
# the download flow is pinned in tests/test_24_ig_pinterest_flow.py).


# --- Spotify --------------------------------------------------------------------------

@pytest.mark.usefixtures("test_tg")
class TestSpotify:
    def setup_method(self, method):
        import bot.handlers.music.spotify.download as m

        self.m = m

    async def test_back_button(self, test_tg):
        update = make_update("Back 🔙", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        # typed Back -> fresh Home (user's own message can't be edited,
        # so go_home sends a new message), input mode is preserved
        assert result == SEARCH_INPUT_STATE
        context.bot.send_message.assert_awaited_once()
        update.message.reply_text.assert_not_awaited()

    async def test_non_url_skips_download(self, test_tg, monkeypatch):
        from bot.handlers.music.spotify import search as spotify_search
        run_search = AsyncMock()
        monkeypatch.setattr(spotify_search, "run_search", run_search)
        update = make_update("just a search term", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == SEARCH_INPUT_STATE
        # plain text goes to the live search flow, never the downloader
        run_search.assert_awaited_once()
        assert run_search.await_args.args[2] == "just a search term"
        context.bot.send_audio.assert_not_awaited()
        context.bot.send_photo.assert_not_awaited()


# --- AI chat ---------------------------------------------------------------------------

@pytest.mark.usefixtures("test_tg")
class TestAiChat:
    def setup_method(self, method):
        from bot.handlers.ai import chat as m

        self.m = m

    async def test_reply_sent_as_html(self, test_tg, monkeypatch):
        calls = []

        class FakeLLM:
            async def ainvoke(self, messages):
                calls.append(list(messages))

                class _Resp:
                    content = "Hello <b>there</b>"

                return _Resp()

        monkeypatch.setattr(self.m, "llm", FakeLLM())
        update = make_update("ping", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.AI_CHAT_STATE
        assert calls[0][0] == ("system", self.m.SYSTEM_PROMPT)
        assert calls[0][1] == ("user", "ping")
        sm = context.bot.send_message
        sm.assert_awaited_once()
        kwargs = sm.await_args.kwargs
        assert kwargs["parse_mode"] == "HTML"
        assert "<b>" not in kwargs["text"]  # user/LLM text escaped
        assert "Hello &lt;b&gt;there&lt;/b&gt;" in kwargs["text"]


# --- AI TTI (dormant) --------------------------------------------------------------------

@pytest.mark.usefixtures("test_tg")
class TestAiTtiDormant:
    async def test_tti_handler_fails_on_missing_sdk(self, test_tg):
        # TTI was disabled 2026-10-26: no state is wired to these handlers
        # and the SDKs are not installed. The lazy import must be the first
        # thing to fail if one is ever invoked.
        import bot.handlers.ai.tti as tti

        update = make_update("a cat", test_tg)
        context = make_context()
        with pytest.raises(ModuleNotFoundError):
            await tti.handler(update, context)

    async def test_tti_replicate_handler_fails_on_missing_sdk(self, test_tg):
        import bot.handlers.ai.tti_replicate as tti_rep

        update = make_update("a cat", test_tg)
        context = make_context()
        with pytest.raises(ModuleNotFoundError):
            await tti_rep.handler(update, context)
