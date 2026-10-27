"""#12 chat handlers × 6 — handler-level with mocked bot + fake yt-dlp.

The decorators (sync_user/log_message) run against the live test DB with
unique tg_ids, cleaned up per test.
"""
import asyncio
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.db import SessionLocal
from models.log import Log
from models.user import User

FIXTURES = Path(__file__).parent / "fixtures"
SMALL_MP4 = FIXTURES / "small.mp4"
SMALL_MP3 = FIXTURES / "small.mp3"


def _fresh_tg_id() -> int:
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


@pytest.fixture
def test_tg():
    tg_id = _fresh_tg_id()
    yield tg_id
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
    context.bot = MagicMock()
    for m in (
        "send_message", "send_chat_action", "send_video", "send_audio",
        "send_photo", "editMessageText", "deleteMessage", "set_message_reaction",
    ):
        setattr(context.bot, m, AsyncMock())
    return context


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL; 'downloads' = copies a fixture file."""

    def __init__(self, opts, info, fixture: Path, tmp_path: Path,
                 error_on_extract=False):
        self._info = info
        self._fixture = fixture
        self._tmp = tmp_path
        self._error_on_extract = error_on_extract
        self.downloaded = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if self._error_on_extract:
            from yt_dlp.utils import DownloadError

            raise DownloadError("fake extract failure")
        return self._info

    def download(self, urls):
        for item in self._iter(self._info):
            p = self.prepare_filename(item)
            shutil.copy(self._fixture, p)
            self.downloaded.append(p)

    def process_info(self, info):
        pass

    def prepare_filename(self, info):
        title = info.get("title") or "untitled"
        return str(self._tmp / f"{title}.{info.get('ext', 'mp4')}")

    @staticmethod
    def _iter(info):
        if info.get("_type") == "playlist":
            return list(info.get("entries") or [])
        return [info]


def patch_ydl(monkeypatch, module, fake):
    monkeypatch.setattr(module, "YoutubeDL", lambda opts: fake)


# --- YouTube video ---------------------------------------------------------------

@pytest.mark.usefixtures("test_tg")
class TestYoutubeVideo:
    def setup_method(self, method):
        import bot.handlers.youtube.download as m

        self.m = m

    async def test_back_button(self, test_tg):
        update = make_update("Back 🔙", test_tg)
        context = make_context()
        context.update = update
        result = await self.m.handler(update, context)
        assert result == self.m.INSTAGRAM_STATE
        update.message.reply_text.assert_awaited_once()
        assert update.message.reply_text.await_args.kwargs["reply_markup"] is not None

    async def test_invalid_link(self, test_tg):
        from utils.constants.messages import LINK_IS_INVALID

        update = make_update("hello world", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_DOWNLOAD_STATE
        edit = context.bot.editMessageText
        edit.assert_awaited()
        assert edit.await_args.kwargs["text"] == LINK_IS_INVALID
        context.bot.set_message_reaction.assert_awaited_once()  # 👎

    async def test_single_video_sends_video(self, test_tg, tmp_path, monkeypatch):
        info = {"title": "T1", "ext": "mp4", "thumbnail": "http://t/x.webp"}
        patch_ydl(monkeypatch, self.m, FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
        update = make_update("https://www.youtube.com/watch?v=x", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_DOWNLOAD_STATE
        sv = context.bot.send_video
        sv.assert_awaited_once()
        kwargs = sv.await_args.kwargs
        assert kwargs["duration"] == 2  # int(2.02) from the fixture
        assert kwargs["supports_streaming"] is True
        assert kwargs["protect_content"] is True
        assert kwargs["thumbnail"] == "http://t/x.webp"  # no local thumb -> URL
        assert "T1" in kwargs["caption"]
        context.bot.set_message_reaction.assert_awaited_once()  # 👍

    async def test_extract_error(self, test_tg, tmp_path, monkeypatch):
        from utils.constants.messages import SOMETHING_WENT_WRONG

        fake = FakeYoutubeDL(None, {}, SMALL_MP4, tmp_path, error_on_extract=True)
        patch_ydl(monkeypatch, self.m, fake)
        update = make_update("https://www.youtube.com/watch?v=x", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_DOWNLOAD_STATE
        assert context.bot.send_video.await_count == 0
        edit = context.bot.editMessageText
        assert edit.await_args.kwargs["text"] == SOMETHING_WENT_WRONG

    async def test_playlist_single_entry(self, test_tg, tmp_path, monkeypatch):
        info = {
            "_type": "playlist",
            "title": "PL",
            "entries": [{"title": "E1", "ext": "mp4", "thumbnail": None}],
        }
        patch_ydl(monkeypatch, self.m, FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
        update = make_update("https://www.youtube.com/playlist?list=x", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_DOWNLOAD_STATE
        assert context.bot.send_video.await_count == 1

    async def test_playlist_empty_entries(self, test_tg, tmp_path, monkeypatch):
        from utils.constants.messages import SOMETHING_WENT_WRONG

        info = {"_type": "playlist", "title": "PL", "entries": []}
        patch_ydl(monkeypatch, self.m, FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
        update = make_update("https://www.youtube.com/playlist?list=x", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_DOWNLOAD_STATE
        assert context.bot.send_video.await_count == 0
        assert (
            context.bot.editMessageText.await_args.kwargs["text"]
            == SOMETHING_WENT_WRONG
        )


# --- YouTube mp3 -------------------------------------------------------------------

@pytest.mark.usefixtures("test_tg")
class TestYoutubeMp3:
    def setup_method(self, method):
        import bot.handlers.youtube.mp3 as m

        self.m = m

    async def test_invalid_link(self, test_tg):
        from utils.constants.messages import LINK_IS_INVALID

        update = make_update("nope", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_MP3_STATE
        assert (
            context.bot.editMessageText.await_args.kwargs["text"] == LINK_IS_INVALID
        )

    async def test_single_mp3_sends_audio(self, test_tg, tmp_path, monkeypatch):
        info = {"title": "M1", "ext": "mp3", "thumbnail": "http://t/x.jpg"}
        patch_ydl(monkeypatch, self.m, FakeYoutubeDL(None, info, SMALL_MP3, tmp_path))
        update = make_update("https://www.youtube.com/watch?v=x", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.YOUTUBE_MP3_STATE
        sa = context.bot.send_audio
        sa.assert_awaited_once()
        kwargs = sa.await_args.kwargs
        assert kwargs["duration"] >= 1
        assert kwargs["protect_content"] is True
        context.bot.set_message_reaction.assert_awaited_once()  # 👍


# --- Pinterest ----------------------------------------------------------------------

@pytest.mark.usefixtures("test_tg")
class TestPinterest:
    def setup_method(self, method):
        import bot.handlers.pinterest.download as m

        self.m = m

    async def test_back_button(self, test_tg):
        update = make_update("Back 🔙", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.INSTAGRAM_STATE
        update.message.reply_text.assert_awaited_once()

    async def test_invalid_url(self, test_tg):
        from utils.constants.messages import INVALID_PINTEREST_URL

        update = make_update("https://example.com/not-pinterest", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.PINTEREST_STATE
        kwargs = context.bot.editMessageText.await_args.kwargs
        assert kwargs["text"] == INVALID_PINTEREST_URL
        assert kwargs["parse_mode"] == "HTML"
        context.bot.set_message_reaction.assert_awaited_once()  # 👎


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
        assert result == self.m.START_STATE
        update.message.reply_text.assert_awaited_once()

    async def test_non_url_skips_download(self, test_tg):
        update = make_update("just a search term", test_tg)
        context = make_context()
        result = await self.m.handler(update, context)
        assert result == self.m.MUSIC_SPOTIFY_STATE
        context.bot.send_message.assert_not_awaited()

    async def test_track_download(self, test_tg, tmp_path, monkeypatch):
        # point the download dir at tmp
        monkeypatch.setattr(self.m, "DOWNLOAD_MP3_PATH", str(tmp_path))
        (tmp_path / "track.mp3").write_bytes(SMALL_MP3.read_bytes())

        class FakeSong:
            cover_url = "http://cover/x.jpg"
            json = {"name": "Track", "artist": "Artist"}
            duration = 2000  # ms

        class FakePath:
            name = "track.mp3"

        class FakeSpotdl:
            def __init__(self):
                # downloader.loop is the loop spotdl's SYNC api would run
                # (run_until_complete). In production it IS the bot's own
                # running loop (PTB picks it up after Downloader.__init__'s
                # set_event_loop), which is exactly why the sync api must
                # not be used — it crashed the bot on 2026-10-27.
                self.downloader = SimpleNamespace(
                    loop=asyncio.new_event_loop(),
                    pool_download=self._pool_download,
                )
                self.sync_download_calls = 0

            async def _pool_download(self, song):
                return FakeSong(), FakePath()

            def search(self, terms):
                return [FakeSong()]

            def download(self, song):
                self.sync_download_calls += 1
                raise AssertionError(
                    "sync spotdl.download() used — it runs run_until_complete "
                    "on the bot's own running loop (2026-10-27 crash); "
                    "await downloader.pool_download instead"
                )

        fake = FakeSpotdl()
        update = make_update("https://open.spotify.com/track/x", test_tg)
        context = make_context()
        # a real dict: MagicMock __setitem__ does not store values, so the
        # handler would see an empty mock and skip the whole download loop
        context.application.bot_data = {"spotdl_instance": fake}
        result = await self.m.handler(update, context)
        assert result == self.m.MUSIC_SPOTIFY_STATE
        # regression: the download must go through the async api
        assert fake.sync_download_calls == 0
        sa = context.bot.send_audio
        sa.assert_awaited_once()
        kwargs = sa.await_args.kwargs
        assert kwargs["title"] == "Track"
        assert kwargs["performer"] == "Artist"
        assert kwargs["duration"] == 2
        assert kwargs["thumbnail"] == "http://cover/x.jpg"
        # TASK_DONE with the back keyboard
        last = context.bot.send_message.await_args_list[-1].kwargs
        assert "reply_markup" in last
        context.bot.set_message_reaction.assert_awaited_once()  # 👍

    async def test_download_awaited_on_running_loop_result_not_orphaned(
        self, test_tg, tmp_path, monkeypatch
    ):
        """Regression (2026-10-27 10:03): spotdl's sync download API crashed
        with RuntimeError("This event loop is already running") because PTB
        runs the bot on the very loop spotdl claims as its own (its
        Downloader.__init__ calls set_event_loop in the main thread, and
        Application.__run picks it up via get_event_loop). On top of the
        crash, asyncio.gather() inside download_multiple_songs scheduled the
        download task on the running loop BEFORE run_until_complete checked,
        so the song downloaded as an orphaned task: mp3 on disk, no
        sendAudio, user left with "Something went wrong".

        The handler must await the async api on the running loop so the
        result comes straight back to the handler.
        """
        monkeypatch.setattr(self.m, "DOWNLOAD_MP3_PATH", str(tmp_path))
        (tmp_path / "track.mp3").write_bytes(SMALL_MP3.read_bytes())

        loops = []

        class FakeSong:
            cover_url = "http://cover/x.jpg"
            json = {"name": "Track", "artist": "Artist"}
            duration = 2000  # ms

        class FakePath:
            name = "track.mp3"

        class FakeSpotdl:
            def __init__(self):
                self.downloader = SimpleNamespace(
                    loop=asyncio.new_event_loop(),
                    pool_download=self._pool_download,
                )

            async def _pool_download(self, song):
                loops.append(asyncio.get_running_loop())
                return FakeSong(), FakePath()

            def search(self, terms):
                return [FakeSong()]

        update = make_update("https://open.spotify.com/track/x", test_tg)
        context = make_context()
        context.application.bot_data = {"spotdl_instance": FakeSpotdl()}
        result = await self.m.handler(update, context)
        assert result == self.m.MUSIC_SPOTIFY_STATE
        # the download coroutine ran on the handler's own (running) loop —
        # not in a worker thread with its own run_until_complete
        assert loops, "downloader.pool_download was never awaited"
        assert loops[0] is asyncio.get_running_loop()
        # and the result reached the handler: the audio was actually sent
        context.bot.send_audio.assert_awaited_once()


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
