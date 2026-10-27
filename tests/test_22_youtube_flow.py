"""#22 YouTube download flow (redesign §6.3, §4.2, §4.3) — the shared
media driver: status engine phases (accepted / resolving / slow /
downloading / still-working), task registration, honest cancellation at
item boundaries, single-file delivery, the >50 MB direct-link card,
playlist counts with per-item failures, retry replay, and the MP3 path.

The worker runs on a thread; FakeYoutubeDL stands in for yt-dlp and
'downloads' by copying a fixture file.
"""
import asyncio
import shutil
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from yt_dlp.utils import DownloadError

import bot.handlers.tasks as tasks
import bot.handlers.youtube.download as yt_download
import bot.handlers.youtube.flow as flow
import bot.handlers.youtube.mp3 as yt_mp3
from utils import copy
from utils.helpers import prepare_link
from utils.tasks import CANCELLED, DONE, FAILED, get_registry

FIXTURES = Path(__file__).parent / "fixtures"
SMALL_MP4 = FIXTURES / "small.mp4"
SMALL_MP3 = FIXTURES / "small.mp3"

OWNER_ID = 9_000_000_001
VIDEO_URL = "https://www.youtube.com/watch?v=x"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=x"


def make_update(text, tg_id=OWNER_ID, chat_id=7):
    update = MagicMock()
    msg = MagicMock()
    msg.text = text
    msg.chat_id = chat_id
    msg.message_id = 11
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(
        id=tg_id, first_name="F", last_name="L", username="u"
    )
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    # ONE edit mock shared by the message (flow's event edits) and the
    # bot (the status engine's threshold edits): the tests read the full
    # status timeline from a single, correctly ordered record.
    edit = AsyncMock()
    status = MagicMock()
    status.message_id = 500
    status.delete = AsyncMock()
    status.edit_text = edit
    context.status_message = status
    context.bot = MagicMock()
    for m in ("send_chat_action", "send_video", "send_audio",
              "send_photo", "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    context.bot.edit_message_text = edit
    context.bot.send_message = AsyncMock(return_value=status)
    return context


def make_callback(data, tg_id=OWNER_ID, chat_id=7):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    cq.message = MagicMock(chat_id=chat_id, message_id=600,
                           reply_text=AsyncMock())
    update.callback_query = cq
    update.effective_message = cq.message
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    return update


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL; 'downloads' = copies a fixture
    file, matching the flow's call pattern: extract_info(download=False)
    then process_info(item, download=True) per entry."""

    def __init__(self, opts, info, fixture: Path, tmp_path: Path, *,
                 error_on_extract=False, error_items=(), slow_extract=0.0,
                 gate_items=(), gate_event=None):
        self.opts = opts
        self._info = info
        self._fixture = fixture
        self._tmp = tmp_path
        self._error_on_extract = error_on_extract
        self._error_items = set(error_items)
        self._slow_extract = slow_extract
        self._gate_items = set(gate_items)
        self._gate_event = gate_event
        self.gate_reached = threading.Event()
        self.processed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if self._slow_extract:
            time.sleep(self._slow_extract)
        if self._error_on_extract:
            raise DownloadError("fake extract failure")
        return self._info

    def process_info(self, info, download=False):
        title = info.get("title") or "untitled"
        if title in self._error_items:
            raise DownloadError(f"fake item failure: {title}")
        if title in self._gate_items:
            # stand in for a long download: block until the test
            # releases the gate (lets the test land a cancel mid-item)
            self.gate_reached.set()
            self._gate_event.wait()
        p = self.prepare_filename(info)
        shutil.copy(self._fixture, p)
        self.processed.append(p)

    def prepare_filename(self, info):
        title = info.get("title") or "untitled"
        return str(self._tmp / f"{title}.{info.get('ext', 'mp4')}")


def patch_ydl(monkeypatch, module, fake):
    monkeypatch.setattr(module, "YoutubeDL", lambda opts: fake)


async def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


def _status_markup_row(context):
    """The keyboard row of the accepted (first) status message."""
    return context.bot.send_message.await_args_list[0].kwargs[
        "reply_markup"].inline_keyboard[0]


def _edits(context):
    return [c.kwargs.get("text") or "" for c in
            context.status_message.edit_text.await_args_list]


# --- single video -----------------------------------------------------------------

async def test_single_video_full_flow(monkeypatch, tmp_path):
    """Spec §6.3/§4.2: accepted message with [Cancel download][Tasks],
    then the video itself — no extra 'all done' message, status
    deleted, 👍 on the triggering message, task finished DONE."""
    info = {"title": "T1", "ext": "mp4", "thumbnail": "http://t/x.webp"}
    patch_ydl(monkeypatch, yt_download,
              FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
    update = make_update(VIDEO_URL)
    context = make_context()

    await yt_download.run_video_download(update, context, VIDEO_URL)

    # 0 s: the accepted message with the spec buttons
    first = context.bot.send_message.await_args_list[0].kwargs
    assert first["text"] == copy.YOUTUBE_ACCEPTED
    row = first["reply_markup"].inline_keyboard[0]
    assert row[0].text == copy.BTN_RETRY_DOWNLOAD  # "⏹ Cancel download"
    assert row[1].text == copy.BTN_TASKS
    token = row[0].callback_data.removeprefix("task:cancel:")

    # the file went out
    sv = context.bot.send_video
    sv.assert_awaited_once()
    kwargs = sv.await_args.kwargs
    assert kwargs["duration"] == 2  # int(2.02) from the fixture
    assert kwargs["supports_streaming"] is True
    assert kwargs["protect_content"] is True
    assert kwargs["thumbnail"] == "http://t/x.webp"  # no local thumb -> URL
    assert "T1" in kwargs["caption"]

    # status removed, no extra completion message, 👍 reaction
    context.status_message.delete.assert_awaited_once()
    assert context.bot.send_message.await_count == 1  # only the status message
    context.bot.set_message_reaction.assert_awaited_once()

    # the task ran under its own token and finished done
    registry = get_registry(context)
    task = registry.get(token)
    assert task is not None
    assert task.kind == "youtube_video"
    assert task.status == DONE
    assert task.title == "T1"


async def test_status_stages_follow_the_phase(monkeypatch, tmp_path):
    """Spec §6.3: while resolving the status reads the YouTube wait
    copy at 5/30 s; the download-phase text is an event edit at
    extraction, and the 60 s threshold reads the long-task copy."""
    info = {"title": "Slow Video", "ext": "mp4", "thumbnail": None}
    fake = FakeYoutubeDL(None, info, SMALL_MP4, tmp_path, slow_extract=2.5)
    patch_ydl(monkeypatch, yt_download, fake)

    # compress the standard thresholds so the test runs in ~3 s
    monkeypatch.setattr(flow.progress, "STAGE_RESOLVING", 1)
    monkeypatch.setattr(flow.progress, "STAGE_SLOW", 2)
    monkeypatch.setattr(flow.progress, "STAGE_STILL_WORKING", 5)

    update = make_update(VIDEO_URL)
    context = make_context()
    await yt_download.run_video_download(update, context, VIDEO_URL)

    texts = _edits(context)
    assert copy.YOUTUBE_RESOLVING.format(elapsed=1) in texts
    assert copy.YOUTUBE_SLOW.format(elapsed=2) in texts
    # the event edit at extraction (no item line for a single file)
    assert any(t.startswith("⬇️ Downloading Slow Video…") for t in texts)
    # resolving copy came before the slow copy, which came before the
    # download event
    i_res = texts.index(copy.YOUTUBE_RESOLVING.format(elapsed=1))
    i_slow = texts.index(copy.YOUTUBE_SLOW.format(elapsed=2))
    i_dl = next(i for i, t in enumerate(texts)
                if t.startswith("⬇️ Downloading"))
    assert i_res < i_slow < i_dl


async def test_phase_text_is_phase_and_threshold_aware():
    """The stage callable reads the live phase: downloading before 60 s
    is the downloading line, at 60 s the long-task line; titles are
    HTML-escaped."""
    state = {"title": "A & B <c>", "downloading": False, "input": "u"}
    assert flow._phase_text(1, state) == copy.YOUTUBE_RESOLVING.format(elapsed=1)
    assert flow._phase_text(35, state) == copy.YOUTUBE_SLOW.format(elapsed=35)
    state["downloading"] = True
    mid = flow._phase_text(35, state)
    assert mid == copy.TASK_DOWNLOADING.format(
        short_title="A &amp; B &lt;c&gt;", elapsed=35
    )
    late = flow._phase_text(60, state)
    assert late == copy.TASK_STILL_WORKING.format(
        short_title="A &amp; B &lt;c&gt;", elapsed=60
    )


async def test_extract_error_sends_invalid_link_card(monkeypatch, tmp_path):
    """Spec §6.3: an unresolvable/invalid YouTube URL gets the exact
    error copy with [Retry][Home] — the task stores the URL for a
    safe replay under a new token."""
    fake = FakeYoutubeDL(None, {}, SMALL_MP4, tmp_path, error_on_extract=True)
    patch_ydl(monkeypatch, yt_download, fake)
    update = make_update(VIDEO_URL)
    context = make_context()

    await yt_download.run_video_download(update, context, VIDEO_URL)

    assert context.bot.send_video.await_count == 0
    last = context.status_message.edit_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INVALID_YOUTUBE
    row = last["reply_markup"].inline_keyboard[0]
    assert row[0].text == copy.BTN_RETRY
    token = row[0].callback_data.removeprefix("task:retry:")
    assert row[1].callback_data == "nav:home"

    registry = get_registry(context)
    task = registry.get(token)
    assert task.status == FAILED
    assert task.retry_payload == {"urls": [VIDEO_URL], "max_height": 720}


async def test_retry_replays_the_stored_payload(monkeypatch, tmp_path):
    """Spec §7.3: Retry re-runs the stored payload under a NEW task —
    the original token stays terminal."""
    fake = FakeYoutubeDL(None, {}, SMALL_MP4, tmp_path, error_on_extract=True)
    patch_ydl(monkeypatch, yt_download, fake)
    update = make_update(VIDEO_URL)
    context = make_context()
    await yt_download.run_video_download(update, context, VIDEO_URL)

    token = context.status_message.edit_text.await_args.kwargs[
        "reply_markup"].inline_keyboard[0][0].callback_data.removeprefix(
        "task:retry:")

    # the retry finds the backend healthy
    info = {"title": "T1", "ext": "mp4", "thumbnail": None}
    patch_ydl(monkeypatch, yt_download,
              FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
    await tasks.on_task_retry(make_callback(f"task:retry:{token}"), context)

    context.bot.send_video.assert_awaited_once()
    registry = get_registry(context)
    assert registry.get(token).status == FAILED  # the original stays terminal
    # the replay registered a second task, finished done
    replayed = [t for t in registry._tasks.values()
                if t.status == DONE and t.kind == "youtube_video"]
    assert len(replayed) == 1


async def test_retry_stale_token_toasts():
    update = make_callback("task:retry:deadbeef")
    context = make_context()
    await tasks.on_task_retry(update, context)
    assert update.callback_query.answer.await_args.args[0] == copy.TOAST_TASK_STALE
    context.bot.send_message.assert_not_awaited()


async def test_retry_active_task_toasts():
    """Retrying a RUNNING task is refused (it is still doing its job)."""
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_ID, chat_id=7, kind="youtube_video", input="u"
    )
    update = make_callback(f"task:retry:{task.token}")
    await tasks.on_task_retry(update, context)
    assert update.callback_query.answer.await_args.args[0] == copy.TOAST_TASK_STALE
    context.bot.send_message.assert_not_awaited()


async def test_retry_foreign_owner_toasts():
    """A task belongs to its owner: a foreign tap never replays it."""
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=424242, chat_id=7, kind="youtube_video", input="u"
    )
    update = make_callback(f"task:retry:{task.token}", tg_id=OWNER_ID)
    await tasks.on_task_retry(update, context)
    assert update.callback_query.answer.await_args.args[0] == copy.TOAST_TASK_STALE


# --- size boundary (spec §6.3/§6.6) ---------------------------------------------

async def test_too_large_file_goes_out_as_link_card(monkeypatch, tmp_path):
    """>=50 MB: no file send, the direct-link card with the copy button,
    and the honest 'no known expiry' line. The file is kept — the link
    points at it."""
    info = {"title": "Big", "ext": "mp4", "thumbnail": None}
    fake = FakeYoutubeDL(None, info, SMALL_MP4, tmp_path)
    patch_ydl(monkeypatch, yt_download, fake)
    monkeypatch.setattr(
        "os.path.getsize", lambda p: 60 * 1024 * 1024
    )
    update = make_update(VIDEO_URL)
    context = make_context()

    await yt_download.run_video_download(update, context, VIDEO_URL)

    context.bot.send_video.assert_not_awaited()
    card = context.bot.send_message.await_args_list[-1].kwargs
    assert copy.ERROR_TOO_LARGE in card["text"]
    assert copy.ERROR_TOO_LARGE_NO_KNOWN_EXPIRY in card["text"]
    row = card["reply_markup"].inline_keyboard[0]
    assert row[0].text == copy.BTN_COPY_LINK
    # CopyTextButton carries the prepared link of the kept file
    assert row[0].to_dict()["copy_text"]["text"] == \
        prepare_link(str(tmp_path / "Big.mp4"))
    assert row[1].callback_data == "nav:home"
    assert row[2].callback_data == "nav:tasks"

    # the staging file survives (the link serves it)
    assert (tmp_path / "Big.mp4").is_file()

    task = get_registry(context).get(
        context.bot.send_message.await_args_list[0].kwargs[
            "reply_markup"].inline_keyboard[0][0].callback_data
        .removeprefix("task:cancel:")
    )
    assert task.status == DONE
    assert task.note == "link"


# --- playlists --------------------------------------------------------------------

def _playlist_info(entries):
    return {"_type": "playlist", "title": "PL", "entries": entries}


async def test_playlist_sends_each_item_and_summarizes(monkeypatch, tmp_path):
    """Spec §6.3/§4.2: per-item delivery while running, then the
    'Finished: n/total' summary on the status message."""
    entries = [
        {"title": "E1", "ext": "mp4", "url": "https://youtu.be/1"},
        {"title": "E2", "ext": "mp4", "url": "https://youtu.be/2"},
    ]
    patch_ydl(monkeypatch, yt_download,
              FakeYoutubeDL(None, _playlist_info(entries), SMALL_MP4, tmp_path))
    update = make_update(PLAYLIST_URL)
    context = make_context()

    await yt_download.run_video_download(update, context, PLAYLIST_URL)

    context.bot.send_video.assert_awaited()
    assert context.bot.send_video.await_count == 2
    # the status was edited into the summary, not deleted
    context.status_message.delete.assert_not_awaited()
    last = context.status_message.edit_text.await_args
    assert f"2/2" in last.kwargs["text"]
    assert copy.TASK_PLAYLIST_FAILED_LINE not in last.kwargs["text"]
    # per-item status lines were shown while running
    assert any("item 1/2" in t for t in _edits(context))
    assert any("item 2/2" in t for t in _edits(context))
    # no retry button when nothing failed
    row = last.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.text for b in row] == [copy.BTN_TASKS, copy.BTN_HOME]

    task = get_registry(context).get(
        context.bot.send_message.await_args_list[0].kwargs[
            "reply_markup"].inline_keyboard[0][0].callback_data
        .removeprefix("task:cancel:")
    )
    assert task.status == DONE
    assert task.item_total == 2
    assert task.item_done == 2


async def test_playlist_failed_item_counted_and_retryable(monkeypatch, tmp_path):
    """One bad item never kills the playlist: the summary counts it,
    and Retry replays only the failed item's URL (spec §7.3)."""
    entries = [
        {"title": "E1", "ext": "mp4", "url": "https://youtu.be/1"},
        {"title": "E2", "ext": "mp4", "url": "https://youtu.be/2"},
    ]
    patch_ydl(monkeypatch, yt_download, FakeYoutubeDL(
        None, _playlist_info(entries), SMALL_MP4, tmp_path,
        error_items={"E2"},
    ))
    update = make_update(PLAYLIST_URL)
    context = make_context()

    await yt_download.run_video_download(update, context, PLAYLIST_URL)

    assert context.bot.send_video.await_count == 1  # only E1 delivered
    last = context.status_message.edit_text.await_args.kwargs
    assert "1/2" in last["text"]
    assert "1 track(s) couldn't be downloaded" in last["text"]
    row = last["reply_markup"].inline_keyboard[0]
    assert row[0].text == copy.BTN_RETRY_FAILED

    token = row[0].callback_data.removeprefix("task:retry:")
    task = get_registry(context).get(token)
    assert task.status == DONE
    assert task.retry_payload == {
        "urls": ["https://youtu.be/2"], "max_height": 720
    }


async def test_cancel_mid_playlist_stops_before_next_item(monkeypatch, tmp_path):
    """Spec §4.3: cancel during item 2's download keeps the delivered
    item 1, discards item 2 (the worker can't be interrupted inside a
    download), and reports the count honestly."""
    entries = [
        {"title": "E1", "ext": "mp4", "url": "https://youtu.be/1"},
        {"title": "E2", "ext": "mp4", "url": "https://youtu.be/2"},
    ]
    gate = threading.Event()
    fake = FakeYoutubeDL(None, _playlist_info(entries), SMALL_MP4, tmp_path,
                         slow_extract=0.2, gate_items={"E2"}, gate_event=gate)
    patch_ydl(monkeypatch, yt_download, fake)
    update = make_update(PLAYLIST_URL)
    context = make_context()

    run = asyncio.create_task(
        yt_download.run_video_download(update, context, PLAYLIST_URL)
    )
    registry = get_registry(context)
    await _wait_for(lambda: registry.all_active()
                    and registry.all_active()[0].kind == "youtube_video")

    # E1 is out; the worker is now stuck inside E2's "download" (gate)
    await _wait_for(lambda: context.bot.send_video.await_count >= 1)
    await _wait_for(lambda: fake.gate_reached.is_set())
    task = registry.all_active()[0]
    tasks._cancel_task(task, update, context)
    gate.set()  # E2's "download" finishes; the flow must not deliver it
    await run

    assert context.bot.send_video.await_count == 1  # only E1 went out
    assert not (tmp_path / "E2.mp4").exists()  # E2 staging file cleaned
    last = context.status_message.edit_text.await_args.kwargs
    assert last["text"] == copy.TASK_PLAYLIST_CANCELLED.format(
        success_count=1, total_count=2)
    # the honest 'requested' state was shown before the confirmation
    assert copy.TASK_CANCEL_REQUESTED in _edits(context)
    assert get_registry(context).get(task.token).status == CANCELLED


async def test_cancel_single_video_discards_the_file(monkeypatch, tmp_path):
    """A single file cannot be interrupted mid-download: the worker
    finishes the current file, the flow then discards it (no send) and
    cleans the staging file."""
    info = {"title": "Solo", "ext": "mp4", "thumbnail": None}
    fake = FakeYoutubeDL(None, info, SMALL_MP4, tmp_path, slow_extract=0.2)
    patch_ydl(monkeypatch, yt_download, fake)
    update = make_update(VIDEO_URL)
    context = make_context()

    run = asyncio.create_task(
        yt_download.run_video_download(update, context, VIDEO_URL)
    )
    registry = get_registry(context)
    await _wait_for(lambda: registry.all_active())
    # cancel while the worker is still resolving
    tasks._cancel_task(registry.all_active()[0], update, context)
    await run

    context.bot.send_video.assert_not_awaited()
    assert not (tmp_path / "Solo.mp4").exists()  # staging file removed
    last = context.status_message.edit_text.await_args.kwargs
    assert last["text"] == copy.TASK_CANCELLED.format(completed_count=0)
    # after cancel the keyboard is the neutral home/tasks row
    row = last["reply_markup"].inline_keyboard[0]
    assert row[0].callback_data == "nav:home"
    token = context.bot.send_message.await_args_list[0].kwargs[
        "reply_markup"].inline_keyboard[0][0].callback_data.removeprefix(
            "task:cancel:")
    assert registry.get(token).status == CANCELLED


# --- MP3 path ---------------------------------------------------------------------

async def test_mp3_single_flow(monkeypatch, tmp_path):
    """The picker's Audio option: same engine, send_audio with title and
    real duration, task kind youtube_mp3."""
    info = {"title": "M1", "ext": "mp3", "thumbnail": "http://t/x.jpg"}
    patch_ydl(monkeypatch, yt_mp3,
              FakeYoutubeDL(None, info, SMALL_MP3, tmp_path))
    update = make_update(VIDEO_URL)
    context = make_context()

    await yt_mp3.run_mp3_download(update, context, VIDEO_URL)

    sa = context.bot.send_audio
    sa.assert_awaited_once()
    kwargs = sa.await_args.kwargs
    assert kwargs["title"] == "M1"
    assert kwargs["duration"] >= 1
    assert kwargs["protect_content"] is True
    assert "M1" in kwargs["caption"]
    context.status_message.delete.assert_awaited_once()

    token = context.bot.send_message.await_args_list[0].kwargs[
        "reply_markup"].inline_keyboard[0][0].callback_data.removeprefix(
        "task:cancel:")
    task = get_registry(context).get(token)
    assert task.kind == "youtube_mp3"
    assert task.status == DONE


async def test_worker_filters_none_playlist_entries(monkeypatch, tmp_path):
    """yt-dlp playlist entries can be None (broken links) — they are
    filtered out, never counted as failures."""
    entries = [
        {"title": "E1", "ext": "mp4", "url": "https://youtu.be/1"},
        None,
    ]
    patch_ydl(monkeypatch, yt_download,
              FakeYoutubeDL(None, _playlist_info(entries), SMALL_MP4, tmp_path))
    update = make_update(PLAYLIST_URL)
    context = make_context()

    await yt_download.run_video_download(update, context, PLAYLIST_URL)

    assert context.bot.send_video.await_count == 1
    # the filtered playlist collapsed to one item: single-item semantics
    # — the file is the answer, the status is removed, no summary
    context.status_message.delete.assert_awaited_once()
    token = context.bot.send_message.await_args_list[0].kwargs[
        "reply_markup"].inline_keyboard[0][0].callback_data.removeprefix(
            "task:cancel:")
    task = get_registry(context).get(token)
    assert task.status == DONE
    assert task.item_total == 1  # the None entry is not in the total
