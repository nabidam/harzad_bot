"""Shared media-download driver (UX redesign §6.3, §4.2, §4.3).

Drives the YouTube video and MP3 flows (and TikTok links, which reuse
the video engine through the universal dispatcher).

One status message per task, kept alive by the status engine
(utils/progress) and edited at meaningful thresholds and at phase
changes. The blocking yt-dlp work runs on a worker thread, so a slow
video never blocks the event loop and the cancel token stays
responsive; the worker pushes phase events to the loop, which does all
Bot API calls.

Cancellation is best-effort and honest (spec §4.3): the worker
confirms at the next interruptible boundary — after the current file
for a single item, before the next item for a playlist. Until then the
status reads "cancellation requested", never a false "stopped".

Files stay local only as staging: a delivered file is deleted right
after Telegram accepts it; an undelivered one (cancelled, failed) is
deleted at the end. A file that goes out as a direct link (>50 MB,
spec §6.6) is kept — the link points at it.

Worker contract: the worker is a blocking function
`(url, opts, cancel_flag, push) -> outcome`. It pushes phase events
(`resolved`, `item_start`, `item_done`, `item_failed`) via `push`, and
MUST push `("_sentinel", outcome)` in a `finally` block — the sentinel
is the flow's completion signal (the executor future's loop wake can
beat the worker's last push, so the future alone cannot end the flow).
"""
import asyncio
import threading
from logging import getLogger

from telegram.constants import ParseMode
from telegram.error import TelegramError

from utils import copy, keyboards, progress
from utils.helpers import escape_html, react_message
from utils.storage import cleanup_media
from utils.tasks import CANCELLED, DONE, FAILED, get_registry

logger = getLogger(__name__)

# Spec §6.6: files at or above this size go out as a direct link.
MAX_SEND_MB = 50

# The worker's terminal event (see module docstring: the worker must
# push (SENTINEL, outcome) in a finally block).
SENTINEL = "_sentinel"


def _phase_text(elapsed: int, state: dict) -> str:
    """Phase-aware text for the engine's threshold edits (spec §6.3).

    The same threshold reads differently while the engine is still
    resolving the link vs. already downloading: resolving follows the
    YouTube wait copy, downloading the shared long-task copy — and
    neither invents a phase that hasn't happened.
    """
    if state["downloading"]:
        subject = state["title"] or state["input"]
        short = escape_html(subject[:60])
        if elapsed >= progress.STAGE_STILL_WORKING:
            return copy.TASK_STILL_WORKING.format(short_title=short, elapsed=elapsed)
        return copy.TASK_DOWNLOADING.format(short_title=short, elapsed=elapsed)
    if elapsed >= progress.STAGE_SLOW:
        return copy.YOUTUBE_SLOW.format(elapsed=elapsed)
    return copy.YOUTUBE_RESOLVING.format(elapsed=elapsed)


def _downloading_text(task, item_title: str | None, total: int | None) -> str:
    """Event-driven status text at the download phase (spec §6.3):
    'Downloading {title}…' with 'item {current}/{total}' when a
    playlist count is known."""
    subject = item_title or task.title or task.input
    text = copy.TASK_DOWNLOADING.format(
        short_title=escape_html(subject[:60]), elapsed=task.elapsed()
    )
    if total and total > 1:
        text += f"\nitem {max(1, task.item_done + 1)}/{total}"
    return text


async def _set_status(status, text: str, markup=None) -> None:
    """Best-effort in-place status edit (the user may have deleted it)."""
    try:
        await status.edit_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    except TelegramError:
        logger.warning("could not edit the status message", exc_info=True)


async def _on_cancel_requested(status, task) -> None:
    """The user tapped Cancel: switch to the honest 'requested' copy and
    drop the Cancel button (it cannot be un-requested) — spec §4.3."""
    await _set_status(status, copy.TASK_CANCEL_REQUESTED,
                      keyboards.home_tasks_row_keyboard())
    task.touch(phase="cancelling")


def _cleanup_undelivered(outcome: dict, handled_paths: set) -> None:
    """Delete staging files that never went out (cancelled, failed, or
    skipped after a late cancel). Delivered files are already cleaned
    by the deliver step; link-served files are kept — the link points
    at them."""
    for item in outcome.get("items") or []:
        if item["path"] not in handled_paths:
            cleanup_media(item["path"])


async def run_media_flow(
    update, context, *, url: str, kind: str, opts: dict,
    worker, deliver, max_height: int | None = None,
) -> None:
    """Run one media download task end to end.

    `worker` is a blocking function `(url, opts, cancel_flag, push) ->
    outcome` run on a worker thread; `deliver` is an async
    `(context, update, chat_id, item_result) -> "sent"|"linked"` for
    the loop side. The task is registered in the TaskRegistry up front
    so the Tasks view and `task:cancel:<token>` work from the first
    second.
    """
    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None
    chat_id = update.effective_chat.id
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=update.effective_user.id,
        chat_id=chat_id,
        kind=kind,
        input=url,
        retry_payload={"urls": [url], "max_height": max_height},
    )
    task.touch(phase="resolving")

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=copy.YOUTUBE_ACCEPTED,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.task_cancel_keyboard(
            task.token, cancel_label=copy.BTN_RETRY_DOWNLOAD
        ),
    )
    task.touch(status_message_id=status.message_id)

    # The stage callables read this; the worker-side phase change flips
    # `downloading` and the next threshold edit picks it up.
    state = {"title": None, "downloading": False, "input": url}
    stages = [
        (progress.STAGE_RESOLVING, lambda e: _phase_text(e, state)),
        (progress.STAGE_SLOW, lambda e: _phase_text(e, state)),
        (progress.STAGE_STILL_WORKING, lambda e: _phase_text(e, state)),
    ]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel_flag = threading.Event()

    def push(event) -> None:
        # worker thread -> loop: schedule the event onto the loop
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def _bridge() -> None:
        # loop-side cancel -> worker-side flag (threading.Event)
        await task.cancel_event.wait()
        cancel_flag.set()

    bridge = loop.create_task(_bridge())
    cancel_watch = asyncio.create_task(task.cancel_event.wait())

    delivered = 0
    linked = 0
    handled_paths: set[str] = set()

    async def _on_event(event) -> None:
        nonlocal delivered, linked
        name = event[0]
        if name == "resolved":
            _, title, total = event
            state.update(title=title, downloading=True)
            task.touch(title=title, item_total=total, phase="downloading")
            await _set_status(status, _downloading_text(task, None, total))
        elif name == "item_start":
            _, index, total, item_title = event
            task.touch(item_done=index - 1)
            await _set_status(status, _downloading_text(task, item_title, total))
        elif name == "item_done":
            _, index, total, item_result = event
            if cancel_flag.is_set():
                # cancelled before this file went out: discard it, the
                # finalize step cleans the staging file
                task.touch(item_done=index)
                return
            try:
                result = await deliver(context, update, chat_id, item_result)
            except TelegramError:
                # one failed Telegram send must not kill a playlist; a
                # single item has no next step, so it is fatal there
                logger.warning("delivery failed for %r", item_result.get("title"),
                               exc_info=True)
                if total and total > 1:
                    task.touch(item_done=index, item_failed=task.item_failed + 1)
                    return
                raise
            delivered += 1
            if result == "linked":
                linked += 1
            handled_paths.add(item_result["path"])
            task.touch(item_done=index)
        elif name == "item_failed":
            _, index, total, item_title = event
            task.touch(item_done=index, item_failed=task.item_failed + 1)

    # The worker signals completion with the SENTINEL event pushed on
    # the same queue as its phase events — not with the executor
    # future's completion. The future's loop wake can land before the
    # worker's last call_soon_threadsafe push runs, and a queue drain
    # at that moment would silently lose the final event (the last
    # item_done). A sentinel riding the queue cannot be lost: the loop
    # keeps consuming until it arrives.

    outcome = None
    cancel_notified = False
    get_event = None
    worker_fut = None
    try:
        async with progress.status_ticker(
            context.bot, chat_id, status.message_id, stages=stages,
            parse_mode=ParseMode.HTML,
        ):
            worker_fut = asyncio.wrap_future(
                loop.run_in_executor(None, worker, url, opts, cancel_flag, push)
            )
            while outcome is None:
                if get_event is not None and not get_event.done():
                    get_event.cancel()
                get_event = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {worker_fut, get_event, cancel_watch},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if get_event in done:
                    event = get_event.result()
                    if event[0] == SENTINEL:
                        outcome = event[1]
                    else:
                        await _on_event(event)
                if cancel_watch in done and not cancel_notified:
                    cancel_notified = True
                    await _on_cancel_requested(status, task)
            if worker_fut.done() and not worker_fut.cancelled():
                # a worker that raised still pushed its sentinel (its
                # finally block) — the exception is only for logging
                try:
                    worker_fut.result()
                except Exception:
                    logger.exception("media worker crashed for %s", url)
                    # the status gets the generic error card (the worker's
                    # finally still pushed its sentinel, so any items that
                    # DID deliver already went out); undelivered staging
                    # files are cleaned
                    await _set_status(
                        status, copy.ERROR_UNEXPECTED,
                        keyboards.retry_home_keyboard(task.token),
                    )
                    registry.finish(task.token, FAILED, note="internal error")
                    _cleanup_undelivered(outcome, handled_paths)
                    return
    finally:
        bridge.cancel()
        cancel_watch.cancel()
        pending = [bridge, cancel_watch]
        if get_event is not None and not get_event.done():
            get_event.cancel()
            pending.append(get_event)
        await asyncio.gather(*pending, return_exceptions=True)

    # --- finalize: the status message becomes the final answer ---------
    total = outcome.get("total")
    failed = outcome.get("failed") or []

    if outcome.get("error") == "extract" or outcome.get("error") == "empty":
        await _set_status(
            status, copy.ERROR_INVALID_YOUTUBE,
            keyboards.retry_home_keyboard(task.token),
        )
        registry.finish(task.token, FAILED, note="invalid link")
        return

    if cancel_flag.is_set():
        if total and total > 1:
            text = copy.TASK_PLAYLIST_CANCELLED.format(
                success_count=delivered, total_count=total
            )
        else:
            text = copy.TASK_CANCELLED.format(completed_count=delivered)
        await _set_status(status, text, keyboards.home_tasks_row_keyboard())
        registry.finish(task.token, CANCELLED, note=f"{delivered}/{total or 1}")
    elif total == 1:
        # no extra "all done" message (spec §4.2): the file is the answer
        await status.delete()
        await react_message(update, context.bot, "👍")
        registry.finish(
            task.token, DONE,
            note="link" if linked else "sent",
        )
    else:
        failure_line = (
            copy.TASK_PLAYLIST_FAILED_LINE.format(failed_count=len(failed))
            if failed else ""
        )
        text = copy.TASK_PLAYLIST_DONE.format(
            success_count=delivered, total_count=total or 0,
            failure_line=failure_line,
        )
        await _set_status(
            status, text,
            keyboards.task_result_keyboard(task.token, retry_failed=bool(failed)),
        )
        registry.finish(
            task.token, DONE if delivered else FAILED,
            note=f"{delivered}/{total or 0}",
        )

    # Retry replays only the failed items (spec §7.3) when they are known
    if failed:
        urls = [u for _title, u in failed if u]
        if urls:
            task.retry_payload = {"urls": urls, "max_height": max_height}

    _cleanup_undelivered(outcome, handled_paths)
