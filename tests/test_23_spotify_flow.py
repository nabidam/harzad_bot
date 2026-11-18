"""#23 Spotify URL download flow (redesign §6.1, §4.2, §4.3, §4.4, §7.3).

Pins the reworked run_spotify_download: one task + one status message
from the first second, per-track download copy, the 45 s resolve guard,
honest best-effort cancellation, per-item failure isolation (never
silently skipped) and the retry-only-failed payload.
"""
import asyncio
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram.error import TelegramError, TimedOut

import bot.handlers.music.spotify.download as download
import bot.handlers.tasks as tasks
from utils import copy, progress
from utils.tasks import CANCELLED, DONE, FAILED, get_registry

FIXTURES = Path(__file__).parent / "fixtures"
SMALL_MP3 = FIXTURES / "small.mp3"

OWNER_ID = 9_000_000_002
TRACK_URL = "https://open.spotify.com/track/abc"
PLAYLIST_URL = "https://open.spotify.com/playlist/xyz"


def make_song(name, url=None, artist="Artist", duration=200,
              cover="http://cover/x.jpg"):
    return SimpleNamespace(
        name=name, artist=artist, duration=duration,
        cover_url=cover, url=url or f"https://open.spotify.com/track/{name}",
        json={"name": name, "artist": artist},
    )


def make_context(fake):
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {"spotdl_instance": fake}
    # ONE edit mock shared by the message (flow edits) and the bot
    # (status-engine threshold edits): the tests read the full status
    # timeline from a single, correctly ordered record.
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


def make_update(url, tg_id=OWNER_ID, chat_id=7):
    update = MagicMock()
    msg = MagicMock(chat_id=chat_id, message_id=100, text=url,
                    reply_text=AsyncMock())
    update.message = msg
    update.effective_message = msg
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    update.callback_query = None
    return update


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


class FakeSpotdl:
    """Stands in for spotdl: blocking search() (runs in an executor like
    production) and async downloader.pool_download(song) awaited on the
    running loop (the 2026-10-27 regression the flow must keep)."""

    def __init__(self, songs, tmp_path: Path, *, resolve_error=False,
                 slow_resolve=0.0, error_tracks=(), gate_tracks=(),
                 gate_event=None, slow_track=0.0):
        self._songs = list(songs)
        self._tmp = tmp_path
        self._resolve_error = resolve_error
        self._slow_resolve = slow_resolve
        self._error_tracks = set(error_tracks)
        self._gate_tracks = set(gate_tracks)
        # asyncio.Event on purpose: pool_download is awaited ON the loop
        # (unlike the YouTube fake, whose process_info blocks a worker
        # thread), so a threading.Event here would freeze the loop
        self._gate_event = gate_event
        self._slow_track = slow_track
        self.gate_reached = threading.Event()
        self.loops = []

    def search(self, terms):
        if self._slow_resolve:
            time.sleep(self._slow_resolve)
        if self._resolve_error:
            raise RuntimeError("fake resolve failure")
        return list(self._songs)

    @property
    def downloader(self):
        return SimpleNamespace(pool_download=self._pool_download)

    async def _pool_download(self, song):
        self.loops.append(asyncio.get_running_loop())
        title = song.name
        if title in self._gate_tracks:
            # stand in for a long download: block until the test releases
            # the gate (lets the test land a cancel mid-item)
            self.gate_reached.set()
            await self._gate_event.wait()
        if self._slow_track:
            await asyncio.sleep(self._slow_track)
        if title in self._error_tracks:
            raise RuntimeError(f"fake track failure: {title}")
        dest = self._tmp / f"{title}.mp3"
        shutil.copy(SMALL_MP3, dest)
        return song, dest


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
            context.bot.edit_message_text.await_args_list]


def _status_token(context):
    return _status_markup_row(context)[0].callback_data.removeprefix(
        "task:cancel:")


# --- single track -----------------------------------------------------------------

async def test_single_track_full_flow(tmp_path):
    """A single track: accepted -> track line -> send_audio -> status
    deleted + 👍. No extra completion message (spec §4.2)."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    assert context.bot.send_message.await_count == 1  # only the status
    first = context.bot.send_message.await_args_list[0].kwargs
    assert first["text"] == copy.SPOTIFY_ACCEPTED
    row = _status_markup_row(context)
    assert row[0].text == copy.BTN_RETRY_DOWNLOAD
    assert row[0].callback_data.startswith("task:cancel:")
    assert row[1].callback_data == "nav:tasks"

    # the per-track download line was shown
    texts = _edits(context)
    assert copy.SPOTIFY_TRACK.format(current=1, total=1, title="T1",
                                     elapsed=0) in texts

    sa = context.bot.send_audio
    sa.assert_awaited_once()
    kwargs = sa.await_args.kwargs
    assert kwargs["title"] == "T1"
    assert kwargs["performer"] == "Artist"
    assert kwargs["duration"] == 200
    assert kwargs["thumbnail"] == "http://cover/x.jpg"
    assert kwargs["protect_content"] is False

    # delivered: staging file gone, status removed, 👍 on the trigger
    assert not (tmp_path / "T1.mp3").exists()
    context.status_message.delete.assert_awaited_once()
    context.bot.set_message_reaction.assert_awaited_once()

    task = get_registry(context).get(_status_token(context))
    assert task.status == DONE
    assert task.note == "sent"
    assert task.retry_payload == {"urls": [TRACK_URL]}


# --- playlists --------------------------------------------------------------------

async def test_playlist_per_item_lines_and_summary(tmp_path):
    """Spec §6.1: each item gets its 'track N/M' line; the status ends as
    the playlist summary (no extra message)."""
    songs = [make_song("S1"), make_song("S2")]
    fake = FakeSpotdl(songs, tmp_path)
    context = make_context(fake)
    update = make_update(PLAYLIST_URL)

    await download.run_spotify_download(update, context, PLAYLIST_URL)

    assert context.bot.send_audio.await_count == 2
    texts = _edits(context)
    assert copy.SPOTIFY_TRACK.format(
        current=1, total=2, title="S1", elapsed=0) in texts
    assert copy.SPOTIFY_TRACK.format(
        current=2, total=2, title="S2", elapsed=0) in texts

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_PLAYLIST_DONE.format(
        success_count=2, total_count=2, failure_line="")
    row = last["reply_markup"].inline_keyboard[0]
    assert row[0].callback_data == "nav:tasks"  # no failures -> no Retry

    task = get_registry(context).get(_status_token(context))
    assert task.status == DONE
    assert task.note == "2/2"


async def test_failed_track_counted_and_retry_only_failed(tmp_path):
    """A failing track never kills the rest and is counted in the
    summary; Retry replays ONLY the failed tracks (spec §7.3)."""
    songs = [make_song("S1"), make_song("S2", url="http://trk/2")]
    fake = FakeSpotdl(songs, tmp_path, error_tracks={"S2"})
    context = make_context(fake)
    update = make_update(PLAYLIST_URL)

    await download.run_spotify_download(update, context, PLAYLIST_URL)

    assert context.bot.send_audio.await_count == 1  # only S1 went out
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_PLAYLIST_DONE.format(
        success_count=1, total_count=2,
        failure_line=copy.TASK_PLAYLIST_FAILED_LINE.format(failed_count=1),
    )
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert row[0].callback_data == f"task:retry:{token}"

    task = get_registry(context).get(token)
    assert task.status == DONE
    assert task.retry_payload == {"urls": ["http://trk/2"]}


# --- single-item failure (spec §7.2) ----------------------------------------------

async def test_single_track_download_failure_sends_service_card(tmp_path):
    """A single track that fails to download gets the temporary-service
    card with [Retry][Tasks][Home] (spec §7.2) — never the success
    reaction on nothing, and the task ends FAILED with the track's URL
    as the safe replay payload."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path, error_tracks={"T1"})
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.bot.send_audio.assert_not_awaited()
    context.status_message.delete.assert_not_awaited()
    context.bot.set_message_reaction.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_TEMPORARY_SERVICE.format(
        platform="Spotify")
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:tasks", "nav:home"]

    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert task.retry_payload == {"urls": [song.url]}


async def test_single_track_delivery_failure_sends_unexpected_card(tmp_path):
    """A failed Telegram send of the only track is an internal error
    (spec §7.1 class 6): the unexpected-error card with [Retry][Home],
    the task ends FAILED, the staging file is dropped, and the
    exception never propagates out of the flow."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path)
    context = make_context(fake)
    context.bot.send_audio = AsyncMock(
        side_effect=TelegramError("fake send failure"))
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.status_message.delete.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_UNEXPECTED
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]

    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert task.retry_payload == {"urls": [song.url]}
    assert not (tmp_path / "T1.mp3").exists()  # staging file cleaned


def _timed_out(cause):
    err = TimedOut("Timed out")
    err.__cause__ = cause  # what `raise TimedOut from err` does in PTB
    return err


async def test_single_track_send_read_timeout_counts_as_delivered(tmp_path):
    """Incident 2026-11-17 13:14: a sendAudio whose RESPONSE timed out
    after the body was fully uploaded still lands — the egress proxy
    relays it late, so the "failed" track arrives right when the user
    sends the NEXT link. A read timeout under the TimedOut therefore
    counts as delivered: no failure card, no Retry offer, task DONE,
    and the status becomes the honest in-flight note (no 👍, since the
    arrival was never confirmed)."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path)
    context = make_context(fake)
    context.bot.send_audio = AsyncMock(
        side_effect=_timed_out(httpx.ReadTimeout("read timed out")))
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.status_message.delete.assert_not_awaited()
    context.bot.set_message_reaction.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.SPOTIFY_SENT_IN_FLIGHT
    row = last["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == ["nav:home", "nav:tasks"]

    task = get_registry(context).get(_status_token(context))
    assert task.status == DONE
    assert task.note == "sent"
    assert not (tmp_path / "T1.mp3").exists()  # body already out: file dropped


async def test_single_track_send_write_timeout_is_failure(tmp_path):
    """The mirror case: a timeout while the BODY was still being written
    is a real failure (the file never fully left). The unexpected-error
    card with Retry stands, exactly like any other failed send."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path)
    context = make_context(fake)
    context.bot.send_audio = AsyncMock(
        side_effect=_timed_out(httpx.WriteTimeout("write timed out")))
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_UNEXPECTED
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]

    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert not (tmp_path / "T1.mp3").exists()  # staging file cleaned


async def test_single_track_failure_retry_replays(tmp_path):
    """Spec §7.3: the failure card's Retry re-runs the stored payload
    under a NEW task token — the original token stays terminal, and
    the kind="spotify" replay finds the (now healthy) backend."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path, error_tracks={"T1"})
    context = make_context(fake)
    update = make_update(TRACK_URL)
    await download.run_spotify_download(update, context, TRACK_URL)
    token = _status_token(context)

    # the retry finds the backend healthy
    context.application.bot_data["spotdl_instance"] = FakeSpotdl(
        [song], tmp_path)
    callback = make_callback(f"task:retry:{token}")
    await tasks.on_task_retry(callback, context)

    context.bot.send_audio.assert_awaited_once()
    registry = get_registry(context)
    assert registry.get(token).status == FAILED  # the original stays terminal
    replayed = [t for t in registry._tasks.values()
                if t.status == DONE and t.kind == "spotify"]
    assert len(replayed) == 1


# --- cancellation -----------------------------------------------------------------

async def test_cancel_during_resolve(tmp_path):
    """Cancel while the URL is still resolving: nothing is sent, the
    status becomes the honest 'cancelled' answer."""
    songs = [make_song("T1")]
    fake = FakeSpotdl(songs, tmp_path, slow_resolve=0.5)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    run = asyncio.create_task(
        download.run_spotify_download(update, context, TRACK_URL)
    )
    registry = get_registry(context)
    await _wait_for(lambda: registry.all_active())
    # cancel while the worker is still resolving
    tasks._cancel_task(registry.all_active()[0], update, context)
    await run

    context.bot.send_audio.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_CANCELLED.format(completed_count=0)
    assert registry.get(_status_token(context)).status == CANCELLED


async def test_cancel_slow_resolve_does_not_block_loop(tmp_path):
    """A cancel mid-resolve must not join the worker on the loop thread.

    The old `with ThreadPoolExecutor()` shut down with wait=True in
    __exit__ — a cancel during a slow resolve froze the whole bot until
    the worker's network call returned (44 s in the 2026-10-28
    incident). The worker is now detached: the cancel finalises
    immediately and the late result is simply discarded."""
    fake = FakeSpotdl([make_song("T1")], tmp_path, slow_resolve=3.0)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    run = asyncio.create_task(
        download.run_spotify_download(update, context, TRACK_URL)
    )
    registry = get_registry(context)
    await _wait_for(lambda: registry.all_active())
    tasks._cancel_task(registry.all_active()[0], update, context)

    # the flow finalises the cancel well before the (3 s) worker
    # returns — no join on the loop thread
    await asyncio.wait_for(run, timeout=2.0)

    assert registry.get(_status_token(context)).status == CANCELLED
    context.bot.send_audio.assert_not_awaited()


async def test_cancel_mid_playlist_discards_in_flight_item(tmp_path):
    """Spec §4.3: cancel during item 2's download keeps the delivered
    item 1, shows the honest 'requested' state, then discards item 2
    (spotdl's thread can't be killed) and reports the true count."""
    songs = [make_song("S1"), make_song("S2")]
    gate = asyncio.Event()
    fake = FakeSpotdl(songs, tmp_path, gate_tracks={"S2"}, gate_event=gate)
    context = make_context(fake)
    update = make_update(PLAYLIST_URL)

    run = asyncio.create_task(
        download.run_spotify_download(update, context, PLAYLIST_URL)
    )
    registry = get_registry(context)
    await _wait_for(lambda: registry.all_active())

    # S1 is out; the worker is now stuck inside S2's "download" (gate)
    await _wait_for(lambda: context.bot.send_audio.await_count >= 1)
    await _wait_for(lambda: fake.gate_reached.is_set())
    task = registry.all_active()[0]
    tasks._cancel_task(task, update, context)
    gate.set()  # S2's "download" finishes; the flow must not deliver it
    await run

    assert context.bot.send_audio.await_count == 1  # only S1 went out
    assert not (tmp_path / "S2.mp3").exists()  # S2 staging file cleaned
    texts = _edits(context)
    assert copy.TASK_CANCEL_REQUESTED in texts  # honest 'requested' state
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_PLAYLIST_CANCELLED.format(
        success_count=1, total_count=2)
    assert registry.get(task.token).status == CANCELLED


# --- resolve guard (spec §4.4) ----------------------------------------------------

async def test_resolve_timeout_guard(tmp_path, monkeypatch):
    """A resolve that does not answer by the 45 s guard ends in the
    temporary-service error with Retry — never a claim the backend
    stopped (the thread may still be running)."""
    monkeypatch.setattr(download, "RESOLVE_TIMEOUT_SECONDS", 0.2)
    songs = [make_song("T1")]
    fake = FakeSpotdl(songs, tmp_path, slow_resolve=1.0)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.bot.send_audio.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_TEMPORARY_SERVICE.format(
        platform="Spotify")
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert row[0].callback_data == f"task:retry:{token}"
    assert row[1].callback_data == "nav:tasks"
    assert row[2].callback_data == "nav:home"
    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert task.note == "resolve timeout"


async def test_resolve_finishing_in_grace_is_honoured(tmp_path, monkeypatch):
    """A resolve that finishes during the post-timeout grace is
    honoured, never discarded at the guard line (incident 2026-10-28
    21:39: a 44 s healthy resolve + a 44 s process stop made the local
    timer expire on work that was already done)."""
    monkeypatch.setattr(download, "RESOLVE_TIMEOUT_SECONDS", 0.5)
    songs = [make_song("T1")]
    # the worker answers at ~0.55 s: after the 0.5 s guard, inside the
    # 0.1 s grace
    fake = FakeSpotdl(songs, tmp_path, slow_resolve=0.55)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.bot.send_audio.assert_awaited_once()
    task = get_registry(context).get(_status_token(context))
    assert task.status == DONE
    assert task.note == "sent"


async def test_resolve_failure_temporary_card(tmp_path):
    """A resolve that raises gets the same temporary-service card."""
    songs = [make_song("T1")]
    fake = FakeSpotdl(songs, tmp_path, resolve_error=True)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.bot.send_audio.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_TEMPORARY_SERVICE.format(
        platform="Spotify")
    task = get_registry(context).get(_status_token(context))
    assert task.status == FAILED
    assert task.note == "resolve failed"


async def test_empty_resolve_is_media_unavailable(tmp_path):
    """The URL resolved to nothing: dead/private link (spec §7.1 class
    2), not a service problem — no Retry-Tasks row, plain Retry+Home."""
    fake = FakeSpotdl([], tmp_path)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    context.bot.send_audio.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_MEDIA_UNAVAILABLE
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert row[0].callback_data == f"task:retry:{token}"
    assert row[1].callback_data == "nav:home"
    task = get_registry(context).get(token)
    assert task.status == FAILED
    assert task.note == "not found"


# --- status engine stages ----------------------------------------------------------

async def test_stage_thresholds_slow_and_still_working(tmp_path, monkeypatch):
    """Spec §6.1: at the 30 s measured threshold the status reads the
    slow copy (naming the current track), at 60 s the still-working
    copy (naming the title). Thresholds compressed for the test."""
    monkeypatch.setattr(progress, "STAGE_SLOW", 1)
    monkeypatch.setattr(progress, "STAGE_STILL_WORKING", 2)
    song = make_song("Slow Track")
    fake = FakeSpotdl([song], tmp_path, slow_track=2.5)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    texts = _edits(context)
    i_track = next(i for i, t in enumerate(texts)
                   if "Downloading track 1/1: Slow Track" in t)
    i_slow = texts.index(copy.SPOTIFY_SLOW.format(current=1, total=1))
    i_still = texts.index(copy.SPOTIFY_VERY_SLOW.format(title="Slow Track"))
    assert i_track < i_slow < i_still


# --- regression: async api on the running loop -------------------------------------

async def test_pool_download_runs_on_running_loop(tmp_path):
    """Regression (2026-10-27 10:03): spotdl's sync API crashed the bot
    (run_until_complete on the already-running loop) and orphaned the
    download. pool_download must be awaited on the running loop so the
    result reaches the flow."""
    song = make_song("T1")
    fake = FakeSpotdl([song], tmp_path)
    context = make_context(fake)
    update = make_update(TRACK_URL)

    await download.run_spotify_download(update, context, TRACK_URL)

    assert fake.loops, "downloader.pool_download was never awaited"
    assert fake.loops[0] is asyncio.get_running_loop()
    context.bot.send_audio.assert_awaited_once()
