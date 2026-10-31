"""#24 Instagram + Pinterest flows (redesign §6.2, §6.4, §4.3, §4.4,
§7.1, §7.2, §7.3).

Pins the reworked cores: one task + one status message from the
first second, the platform wait copy on the shared status engine,
the honest best-effort cancel, the 45 s Instagram metadata guard
(detach semantics), the per-class error cards (session / not-found /
user-not-found / invalid / single-item failure), and retry replay
under a new task token.

FakeIG stands in for the blocking instagrapi client (it runs on the
flow's worker thread, like production); Pinterest reuses test_22's
FakeYoutubeDL for the shared video engine.
"""
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError

from instagrapi.exceptions import MediaNotFound, UnknownError, UserNotFound

import bot.handlers.instagram.download as ig
import bot.handlers.pinterest.download as pin
import bot.handlers.tasks as tasks
import bot.handlers.youtube.download as yt_download
from utils import copy, progress
from utils.tasks import CANCELLED, DONE, FAILED, get_registry
from test_22_youtube_flow import FakeYoutubeDL, patch_ydl, SMALL_MP4

OWNER_ID = 9_000_000_003
POST_URL = "https://www.instagram.com/p/abc123/"
STORY_URL = "https://www.instagram.com/stories/someuser/123"
PROFILE_URL = "https://www.instagram.com/someuser/"
PIN_URL = "https://pin.it/abc123"


def make_update(text, tg_id=OWNER_ID, chat_id=7):
    update = MagicMock()
    msg = MagicMock(chat_id=chat_id, message_id=100, text=text,
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


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    # ONE edit mock shared by the status message (flow edits) and the
    # bot (status-engine threshold edits): the tests read the full
    # status timeline from a single, correctly ordered record.
    edit = AsyncMock()
    status = MagicMock()
    status.message_id = 500
    status.delete = AsyncMock()
    status.edit_text = edit
    context.status_message = status
    context.bot = MagicMock()
    for m in ("send_chat_action", "send_video", "send_audio",
              "send_photo", "send_document", "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    context.bot.edit_message_text = edit
    context.bot.send_message = AsyncMock(return_value=status)
    return context


class FakeIG:
    """Stands in for the blocking instagrapi client (media_pk_from_url
    / media_info / story_info / user_info_by_username). Runs on the
    flow's worker thread, like production."""

    def __init__(self, media=None, *, story=None, user=None,
                 not_found=False, user_not_found=False, unknown=False,
                 slow_media=0.0):
        self._media = media
        self._story = story
        self._user = user
        self._not_found = not_found
        self._user_not_found = user_not_found
        self._unknown = unknown
        self._slow_media = slow_media

    def media_pk_from_url(self, url):
        if self._unknown:
            raise UnknownError("fake unknown error")
        return 11

    def media_info(self, pk):
        if self._slow_media:
            time.sleep(self._slow_media)
        if self._not_found:
            raise MediaNotFound()
        return SimpleNamespace(model_dump=lambda: dict(self._media))

    def story_pk_from_url(self, url):
        return 22

    def story_info(self, pk):
        if self._not_found:
            raise MediaNotFound()
        return self._story

    def user_info_by_username(self, username):
        if self._user_not_found:
            raise UserNotFound()
        return SimpleNamespace(model_dump=lambda: dict(self._user))


def photo_media(caption="hi"):
    return {"media_type": 1, "product_type": "feed",
            "caption_text": caption, "thumbnail_url": "http://ig/photo.jpg"}


def album_media(caption="album"):
    return {
        "media_type": 8, "product_type": "feed", "caption_text": caption,
        "resources": [
            {"video_url": "http://ig/1.mp4", "thumbnail_url": None},
            {"video_url": None, "thumbnail_url": "http://ig/2.jpg"},
            {"video_url": None, "thumbnail_url": "http://ig/3.jpg"},
        ],
    }


async def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


def _edits(context):
    return [c.kwargs.get("text") or "" for c in
            context.bot.edit_message_text.await_args_list]


def _status_token(context):
    row = context.bot.send_message.await_args_list[0].kwargs[
        "reply_markup"].inline_keyboard[0]
    return row[0].callback_data.removeprefix("task:cancel:")


# --- Instagram: success paths ---------------------------------------------------------

async def test_ig_photo_post_full_flow(tmp_path, monkeypatch):
    """Spec §6.2/§4.2: accepted status with [Cancel][Tasks], the
    upload line, the photo with the post's own caption (no bot-id
    line), status deleted + 👍, no extra completion message."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(media=photo_media()))
    update = make_update(POST_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, POST_URL)

    kw = context.bot.send_message.await_args.kwargs
    assert kw["text"] == copy.INSTAGRAM_ACCEPTED
    row = kw["reply_markup"].inline_keyboard[0]
    assert row[0].text == copy.BTN_TASK_CANCEL
    assert row[1].callback_data == "nav:tasks"

    context.bot.send_photo.assert_awaited_once()
    pkw = context.bot.send_photo.await_args.kwargs
    assert pkw["photo"] == "http://ig/photo.jpg"
    assert pkw["caption"] == "hi"
    assert pkw["protect_content"] is False
    context.status_message.delete.assert_awaited()
    context.bot.set_message_reaction.assert_awaited_once()
    assert get_registry(context).get(_status_token(context)).status == DONE


async def test_ig_album_per_item_lines_and_summary(tmp_path, monkeypatch):
    """Spec §6.2: a carousel sends items one by one with the
    'Sending item {current}/{total}' line, the post's caption rides on
    the LAST item, and the status becomes the batch summary."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(media=album_media()))
    update = make_update(POST_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, POST_URL)

    context.bot.send_video.assert_awaited_once()
    assert context.bot.send_video.await_args.kwargs[
        "video"] == "http://ig/1.mp4"
    assert context.bot.send_photo.await_count == 2
    assert context.bot.send_photo.await_args_list[0].kwargs[
        "photo"] == "http://ig/2.jpg"
    last_photo = context.bot.send_photo.await_args_list[1].kwargs
    assert last_photo["photo"] == "http://ig/3.jpg"
    assert last_photo["caption"] == "album"  # caption rides on the last

    texts = _edits(context)
    for n in (1, 2, 3):
        assert copy.TASK_ITEM_UPLOADING.format(current=n, total=3) in texts
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_PLAYLIST_DONE.format(
        success_count=3, total_count=3, failure_line="")
    row = last["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == ["nav:tasks", "nav:home"]
    assert get_registry(context).get(_status_token(context)).status == DONE


async def test_ig_story_flow_sends_phase_line(tmp_path, monkeypatch):
    """Spec §6.2: the story phase line appears only when the worker
    enters the story path; a photo+video story is a 2-item batch."""
    story = SimpleNamespace(thumbnail_url="http://ig/story.jpg",
                            video_url="http://ig/story.mp4")
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(story=story))
    update = make_update(STORY_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, STORY_URL)

    assert copy.INSTAGRAM_STORY in _edits(context)
    context.bot.send_photo.assert_awaited_once()
    assert context.bot.send_photo.await_args.kwargs[
        "photo"] == "http://ig/story.jpg"
    context.bot.send_video.assert_awaited_once()
    assert context.bot.send_video.await_args.kwargs[
        "video"] == "http://ig/story.mp4"
    last = context.bot.edit_message_text.await_args.kwargs
    assert "2/2" in last["text"]
    assert get_registry(context).get(_status_token(context)).status == DONE


async def test_ig_photo_story_is_single_item_success(tmp_path, monkeypatch):
    """A story with no video is ONE item: the single-item success path
    (status deleted + 👍), not a 1/1 batch summary."""
    story = SimpleNamespace(thumbnail_url="http://ig/story.jpg",
                            video_url=None)
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(story=story))
    update = make_update(STORY_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, STORY_URL)

    context.bot.send_photo.assert_awaited_once()
    context.bot.send_video.assert_not_awaited()
    context.status_message.delete.assert_awaited()
    context.bot.set_message_reaction.assert_awaited_once()
    assert get_registry(context).get(_status_token(context)).status == DONE


async def test_ig_profile_link_sends_picture(tmp_path, monkeypatch):
    """An instagram.com/<username> link sends the profile picture
    (the profile shape the dispatcher can still deliver)."""
    user = {"profile_pic_url_hd": "http://ig/pp.jpg"}
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(user=user))
    update = make_update(PROFILE_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, PROFILE_URL)

    context.bot.send_photo.assert_awaited_once()
    assert context.bot.send_photo.await_args.kwargs[
        "photo"] == "http://ig/pp.jpg"
    context.status_message.delete.assert_awaited()
    assert get_registry(context).get(_status_token(context)).status == DONE


# --- Instagram: error classes (spec §6.2) ---------------------------------------------

async def test_ig_session_error_card(tmp_path, monkeypatch):
    """A session/cookie/server problem gets the service-side failure
    card with [Retry][Tasks][Home] — and NEVER a negative reaction
    (spec §6.2)."""
    monkeypatch.setattr(ig, "get_user_client_instagram", lambda: None)
    update = make_update(POST_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, POST_URL)

    context.bot.send_photo.assert_not_awaited()
    context.bot.set_message_reaction.assert_not_awaited()  # no 👎
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INSTAGRAM_SESSION
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:tasks", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_ig_post_not_found_card(tmp_path, monkeypatch):
    """A deleted/private post gets the class-2 card with [Retry][Home]."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(not_found=True))
    update = make_update(POST_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, POST_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INSTAGRAM_NOT_FOUND
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_ig_user_not_found_card(tmp_path, monkeypatch):
    """An unknown profile gets the exact user-not-found card with
    [Retry][Home]."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(user_not_found=True))
    update = make_update(PROFILE_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, PROFILE_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INSTAGRAM_USER_NOT_FOUND
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_ig_unknown_error_maps_to_not_found(tmp_path, monkeypatch):
    """An unclassifiable client error is inaccessible content, not a
    service outage (the not-found card)."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(unknown=True))
    update = make_update(POST_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, POST_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INSTAGRAM_NOT_FOUND
    assert get_registry(context).get(_status_token(context)).status == FAILED


async def test_ig_metadata_timeout_guard(tmp_path, monkeypatch):
    """The 45 s metadata/fetch guard (spec §4.4) DETACHES the wait and
    ends in the service-side failure card — the worker thread cannot
    be killed, so the copy never claims the backend stopped.
    Threshold compressed for the test."""
    monkeypatch.setattr(ig, "METADATA_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(media=photo_media(), slow_media=1.0))
    update = make_update(POST_URL)
    context = make_context()

    await ig.run_instagram_download(update, context, POST_URL)

    context.bot.send_photo.assert_not_awaited()
    context.bot.send_video.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INSTAGRAM_SESSION
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:tasks", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_ig_single_delivery_failure_unexpected_card(tmp_path, monkeypatch):
    """A failed Telegram send (and its document fallback) of the only
    item is an internal error (spec §7.1 class 6): the unexpected
    card with [Retry][Home], task FAILED, no propagation."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(media=photo_media()))
    update = make_update(POST_URL)
    context = make_context()
    context.bot.send_photo = AsyncMock(side_effect=TelegramError("fake"))
    context.bot.send_document = AsyncMock(side_effect=TelegramError("fake"))

    await ig.run_instagram_download(update, context, POST_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_UNEXPECTED
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


# --- Instagram: cancel + retry ---------------------------------------------------------

async def test_ig_cancel_mid_album_stops_before_next_item(tmp_path, monkeypatch):
    """Spec §4.3: cancel while item 2's send is in flight keeps items
    1 and 2 (the in-flight send finishes), never starts item 3, shows
    the honest 'requested' state, and reports the true count."""
    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(media=album_media()))
    update = make_update(POST_URL)
    context = make_context()

    reached = asyncio.Event()
    release = asyncio.Event()

    async def slow_photo(*args, **kwargs):
        reached.set()
        await release.wait()
        return MagicMock()

    context.bot.send_photo = AsyncMock(side_effect=slow_photo)

    run = asyncio.create_task(
        ig.run_instagram_download(update, context, POST_URL))
    registry = get_registry(context)
    await _wait_for(lambda: context.bot.send_video.await_count >= 1)
    await _wait_for(lambda: reached.is_set())  # item 2's send in flight
    task = registry.all_active()[0]
    tasks._cancel_task(task, update, context)
    release.set()
    await run

    assert context.bot.send_video.await_count == 1
    assert context.bot.send_photo.await_count == 1  # item 3 never sent
    texts = _edits(context)
    assert copy.TASK_CANCEL_REQUESTED in texts
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.TASK_PLAYLIST_CANCELLED.format(
        success_count=2, total_count=3)
    assert registry.get(task.token).status == CANCELLED


async def test_ig_session_error_retry_replays(tmp_path, monkeypatch):
    """Spec §7.3: the session card's Retry re-runs the stored payload
    under a NEW task token — the original stays terminal, and the
    kind="instagram" replay finds the (now healthy) session."""
    monkeypatch.setattr(ig, "get_user_client_instagram", lambda: None)
    update = make_update(POST_URL)
    context = make_context()
    await ig.run_instagram_download(update, context, POST_URL)
    token = _status_token(context)
    assert get_registry(context).get(token).status == FAILED

    monkeypatch.setattr(ig, "get_user_client_instagram",
                        lambda: FakeIG(media=photo_media()))
    await tasks.on_task_retry(make_callback(f"task:retry:{token}"), context)

    context.bot.send_photo.assert_awaited_once()
    registry = get_registry(context)
    assert registry.get(token).status == FAILED  # the original stays terminal
    replayed = [t for t in registry._tasks.values()
                if t.status == DONE and t.kind == "instagram"]
    assert len(replayed) == 1


# --- Pinterest (shared video engine, spec §6.4) ----------------------------------------

async def test_pinterest_pin_full_flow(monkeypatch, tmp_path):
    """Spec §6.4: the pin runs on the shared video engine with the
    Pinterest wait copy — the accepted line names Pinterest, a single
    file success deletes the status and adds 👍."""
    info = {"title": "Pin", "ext": "mp4", "thumbnail": None}
    patch_ydl(monkeypatch, yt_download,
              FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
    update = make_update(PIN_URL)
    context = make_context()

    await pin.run_pinterest_download(update, context, PIN_URL)

    assert context.bot.send_message.await_args.kwargs[
        "text"] == copy.PINTEREST_ACCEPTED
    context.bot.send_video.assert_awaited_once()
    context.status_message.delete.assert_awaited()
    context.bot.set_message_reaction.assert_awaited_once()
    assert get_registry(context).get(_status_token(context)).status == DONE


async def test_pinterest_invalid_link_card(monkeypatch, tmp_path):
    """Spec §6.4: an unrecognizable pin gets the exact
    invalid-Pinterest card with [Retry][Home]."""
    patch_ydl(monkeypatch, yt_download, FakeYoutubeDL(
        None, {}, SMALL_MP4, tmp_path, error_on_extract=True))
    update = make_update(PIN_URL)
    context = make_context()

    await pin.run_pinterest_download(update, context, PIN_URL)

    assert context.bot.send_video.await_count == 0
    context.status_message.delete.assert_not_awaited()
    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_INVALID_PINTEREST
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_pinterest_single_download_failure_service_card(
        monkeypatch, tmp_path):
    """Spec §6.4/§7: unavailable media/service uses the service-side
    retry error — 'Pinterest is temporarily unavailable' with
    [Retry][Tasks][Home], task FAILED."""
    info = {"title": "P1", "ext": "mp4", "thumbnail": None}
    patch_ydl(monkeypatch, yt_download, FakeYoutubeDL(
        None, info, SMALL_MP4, tmp_path, error_items={"P1"}))
    update = make_update(PIN_URL)
    context = make_context()

    await pin.run_pinterest_download(update, context, PIN_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    assert last["text"] == copy.ERROR_TEMPORARY_SERVICE.format(
        platform="Pinterest")
    row = last["reply_markup"].inline_keyboard[0]
    token = _status_token(context)
    assert [b.callback_data for b in row] == [
        f"task:retry:{token}", "nav:tasks", "nav:home"]
    assert get_registry(context).get(token).status == FAILED


async def test_pinterest_stage_lines_are_platform_specific(
        monkeypatch, tmp_path):
    """Spec §6.4: while resolving the status reads the PINTEREST wait
    copy at the 5 s threshold and the shared long-task copy at 30 s;
    the download-phase line is the engine's event edit."""
    info = {"title": "Pin", "ext": "mp4", "thumbnail": None}
    fake = FakeYoutubeDL(None, info, SMALL_MP4, tmp_path, slow_extract=2.5)
    patch_ydl(monkeypatch, yt_download, fake)
    # the ticker floors elapsed to whole seconds, so the thresholds are
    # integers (the stage fires at its second)
    monkeypatch.setattr(progress, "STAGE_RESOLVING", 1)
    monkeypatch.setattr(progress, "STAGE_SLOW", 2)
    monkeypatch.setattr(progress, "STAGE_STILL_WORKING", 5)

    update = make_update(PIN_URL)
    context = make_context()
    await pin.run_pinterest_download(update, context, PIN_URL)

    texts = _edits(context)
    i_res = texts.index(copy.PINTEREST_RESOLVING.format(elapsed=1))
    i_slow = texts.index(copy.TASK_SLOW.format(elapsed=2))
    i_dl = next(i for i, t in enumerate(texts)
                if t.startswith("⬇️ Downloading"))
    assert i_res < i_slow < i_dl
    context.bot.send_video.assert_awaited_once()


async def test_pinterest_invalid_retry_replays(monkeypatch, tmp_path):
    """Spec §7.3: the invalid-link card's Retry re-runs the stored
    payload under a NEW task token — the original stays terminal, and
    the kind="pinterest" replay finds the (now healthy) engine."""
    patch_ydl(monkeypatch, yt_download, FakeYoutubeDL(
        None, {}, SMALL_MP4, tmp_path, error_on_extract=True))
    update = make_update(PIN_URL)
    context = make_context()
    await pin.run_pinterest_download(update, context, PIN_URL)

    last = context.bot.edit_message_text.await_args.kwargs
    row = last["reply_markup"].inline_keyboard[0]
    token = row[0].callback_data.removeprefix("task:retry:")
    assert get_registry(context).get(token).status == FAILED

    info = {"title": "Pin", "ext": "mp4", "thumbnail": None}
    patch_ydl(monkeypatch, yt_download,
              FakeYoutubeDL(None, info, SMALL_MP4, tmp_path))
    await tasks.on_task_retry(make_callback(f"task:retry:{token}"), context)

    context.bot.send_video.assert_awaited_once()
    registry = get_registry(context)
    assert registry.get(token).status == FAILED  # the original stays terminal
    replayed = [t for t in registry._tasks.values()
                if t.status == DONE and t.kind == "pinterest"]
    assert len(replayed) == 1
