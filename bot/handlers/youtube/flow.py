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


def _phase_text(elapsed: int, state: dict, resolving: str, slow: str) -> str:
    """Phase-aware text for the engine's threshold edits (spec §6.3).

    The same threshold reads differently while the engine is still
    resolving the link vs. already downloading: resolving follows the
    platform's wait copy (passed in — each platform has its own),
    downloading the shared long-task copy — and neither invents a
    phase that hasn't happened.
    """
    if state["downloading"]:
        subject = state["title"] or state["input"]
        short = escape_html(subject[:60])
        if elapsed >= progress.STAGE_STILL_WORKING:
            return copy.TASK_STILL_WORKING.format(short_title=short, elapsed=elapsed)
        return copy.TASK_DOWNLOADING.format(short_title=short, elapsed=elapsed)
    if elapsed >= progress.STAGE_SLOW:
        return slow.format(elapsed=elapsed)
    return resolving.format(elapsed=elapsed)


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


def _cleanup_undelivered(outcome: dict | None, handled_paths: set) -> None:
    """Delete staging files that never went out (cancelled, failed, or
    skipped after a late cancel). Delivered files are already cleaned
    by the deliver step; link-served files are kept — the link points
    at them. Items without a local path (remote-URL media) have
    nothing to clean."""
    for item in (outcome or {}).get("items") or []:
        path = item.get("path")
        if path and path not in handled_paths:
            cleanup_media(path)


def _platform_name(kind: str) -> str:
    """The user-facing platform word for a flow kind (error copy,
    spec §7.2)."""
    return {"tiktok": "TikTok", "pinterest": "Pinterest"}.get(kind, "YouTube")


async def run_media_flow(
    update, context, *, url: str, kind: str, opts: dict,
    worker, deliver, max_height: int | None = None,
    accepted=None, resolving=None, slow=None, item_line=None,
    cancel_label=None,
    worker_timeout_seconds: float | None = None,
    on_error=None, on_timeout=None,
) -> None:
    """Run one media download task end to end.

    `worker` is a blocking function `(url, opts, cancel_flag, push) ->
    outcome` run on a worker thread; `deliver` is an async
    `(context, update, chat_id, item_result) -> "sent"|"linked"` for
    the loop side. The task is registered in the TaskRegistry up front
    so the Tasks view and `task:cancel:<token>` work from the first
    second.

    Platform hooks (all optional — the defaults are the YouTube
    behavior, so existing callers are unchanged):
    - `accepted` / `resolving` / `slow`: the wait-copy lines for the
      accepted status and the 5 s / 30 s resolving thresholds
      (spec §6.2/§6.4: each platform names itself).
    - `item_line(task, item_title, total)`: the per-item status line
      at each item boundary (default: the shared download line with
      `item N/M`).
    - `worker_timeout_seconds`: the spec §4.4 guard on the worker
      (metadata/fetch). The worker thread cannot be killed, so the
      guard DETACHES: it sets the cancel flag (the worker stops before
      the next item), delivers whatever already completed, and
      finalizes with the timeout card — the copy never claims the
      backend stopped. A worker that finished in time always wins.
    - `on_error(err, token) -> (text, markup) | None`: the error card
      for an outcome error kind (e.g. "auth", "not_found").
    - `on_timeout(token) -> (text, markup) | None`: the timeout card.
    """
    if accepted is None:
        accepted = copy.YOUTUBE_ACCEPTED
    if resolving is None:
        resolving = copy.YOUTUBE_RESOLVING
    if slow is None:
        slow = copy.YOUTUBE_SLOW
    if item_line is None:
        item_line = _downloading_text
    if cancel_label is None:
        cancel_label = copy.BTN_RETRY_DOWNLOAD
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
        text=accepted,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.task_cancel_keyboard(
            task.token, cancel_label=cancel_label
        ),
    )
    task.touch(status_message_id=status.message_id)

    # The stage callables read this; the worker-side phase change flips
    # `downloading` and the next threshold edit picks it up.
    state = {"title": None, "downloading": False, "input": url}
    stages = [
        (progress.STAGE_RESOLVING, lambda e: _phase_text(e, state, resolving, slow)),
        (progress.STAGE_SLOW, lambda e: _phase_text(e, state, resolving, slow)),
        (progress.STAGE_STILL_WORKING, lambda e: _phase_text(e, state, resolving, slow)),
    ]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel_flag = threading.Event()

    async def _set_status(status, text: str, markup=None) -> None:
        """Best-effort in-place status edit (the user may have deleted it)."""
        if markup is None:
            markup = (
                keyboards.task_cancel_keyboard(
                    task.token, cancel_label=cancel_label
                )
                if not cancel_flag.is_set()
                else keyboards.home_tasks_row_keyboard()
            )
        try:
            await status.edit_text(
                text=text, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        except TelegramError:
            logger.warning("could not edit the status message", exc_info=True)

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
    delivery_failed: list[dict] = []

    async def _on_event(event) -> None:
        nonlocal delivered, linked
        name = event[0]
        # The cancel line must never be overwritten by a phase line
        # that landed in the queue before the tap was processed: the
        # honest "requested" copy stands until the worker confirms.
        if name == "status":
            # a platform-specific phase line pushed by the worker (e.g.
            # Instagram's story phase, spec §6.2): plain text, the
            # Cancel button stays
            _, text = event
            if not cancel_flag.is_set():
                await _set_status(status, text)
        elif name == "resolved":
            _, title, total = event
            state.update(title=title, downloading=True)
            task.touch(title=title, item_total=total, phase="downloading")
            if not cancel_flag.is_set():
                await _set_status(status, item_line(task, None, total))
        elif name == "item_start":
            _, index, total, item_title = event
            task.touch(item_done=index - 1)
            if not cancel_flag.is_set():
                await _set_status(status, item_line(task, item_title, total))
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
                # a failed Telegram send is a failed item, like a failed
                # download: counted, retriable, never raised (the
                # finalize step owns the terminal state, so the task
                # can never be left running forever)
                logger.warning("delivery failed for %r", item_result.get("title"),
                               exc_info=True)
                task.touch(item_done=index, item_failed=task.item_failed + 1)
                delivery_failed.append(item_result)
                return
            delivered += 1
            if result == "linked":
                linked += 1
            if item_result.get("path"):
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
    timed_out = False
    get_event = None
    worker_fut = None
    try:
        async with progress.status_ticker(
            context.bot, chat_id, status.message_id, stages=stages,
            reply_markup=lambda: keyboards.task_cancel_keyboard(
                task.token, cancel_label=cancel_label
            ) if not cancel_flag.is_set() else keyboards.home_tasks_row_keyboard(),
            parse_mode=ParseMode.HTML,
        ):
            worker_fut = asyncio.wrap_future(
                loop.run_in_executor(None, worker, url, opts, cancel_flag, push)
            )
            timeout_task = (
                asyncio.create_task(asyncio.sleep(worker_timeout_seconds))
                if worker_timeout_seconds
                else None
            )
            while outcome is None:
                if get_event is not None and not get_event.done():
                    get_event.cancel()
                get_event = asyncio.create_task(queue.get())
                wait_set = {worker_fut, get_event, cancel_watch}
                if timeout_task is not None:
                    wait_set.add(timeout_task)
                done, _ = await asyncio.wait(
                    wait_set,
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
                if timeout_task is not None and timeout_task in done \
                        and not worker_fut.done():
                    # The §4.4 guard fired while the worker is still
                    # running. The worker thread cannot be killed, so
                    # DETACH: tell it to stop before the next item,
                    # deliver whatever already completed (a finished
                    # worker always wins — the sentinel is drained),
                    # and finalize with the timeout card. The copy
                    # never claims the backend stopped (spec §4.4).
                    cancel_flag.set()
                    await asyncio.sleep(0)  # let scheduled puts land
                    while not queue.empty():
                        event = queue.get_nowait()
                        if event[0] == SENTINEL:
                            outcome = event[1]
                        else:
                            await _on_event(event)
                    if outcome is None:
                        timed_out = True
                    break
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

    if timed_out:
        # The §4.4 guard fired: the status becomes the timeout card —
        # never a claim the backend stopped (the worker may still be
        # unwinding in its thread).
        card = on_timeout(task.token) if on_timeout else (
            copy.ERROR_TEMPORARY_SERVICE.format(
                platform=_platform_name(kind)),
            keyboards.retry_tasks_home_keyboard(task.token),
        )
        await _set_status(status, card[0], card[1])
        registry.finish(task.token, FAILED, note="timed_out")
        _cleanup_undelivered(outcome, handled_paths)
        return

    # A delivery failure happens loop-side, where the worker's `failed`
    # list cannot see it: merge it so the summary line and the retry
    # payload count it too (spec §7.3).
    if delivery_failed:
        outcome["failed"].extend(
            (item["title"], item.get("url")) for item in delivery_failed
        )

    # --- finalize: the status message becomes the final answer ---------
    total = outcome.get("total")
    failed = outcome.get("failed") or []

    err = outcome.get("error")
    if err:
        # Platform-specific error cards (e.g. Instagram's session /
        # not-found / user-not-found classes, spec §6.2/§6.4). The
        # extract/empty default is the YouTube invalid-link card.
        card = on_error(err, task.token) if on_error else None
        if card is None and err in ("extract", "empty"):
            card = (copy.ERROR_INVALID_YOUTUBE,
                    keyboards.retry_home_keyboard(task.token))
        if card is None:
            card = (copy.ERROR_UNEXPECTED,
                    keyboards.retry_home_keyboard(task.token))
        await _set_status(status, card[0], card[1])
        registry.finish(task.token, FAILED, note=err)
        _cleanup_undelivered(outcome, handled_paths)
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
    elif total == 1 and delivered:
        # success: no extra "all done" message (spec §4.2) — the file
        # is the answer
        try:
            await status.delete()
        except TelegramError:
            logger.debug("status message already gone", exc_info=True)
        await react_message(update, context.bot, "👍")
        registry.finish(
            task.token, DONE,
            note="link" if linked else "sent",
        )
    elif total == 1:
        # the single item never went out: the status becomes the
        # failure card (spec §7.2) — a failed send is an internal
        # error (class 6), a failed download a temporary service
        # problem (class 3)
        if delivery_failed:
            await _set_status(status, copy.ERROR_UNEXPECTED,
                              keyboards.retry_home_keyboard(task.token))
        else:
            await _set_status(
                status,
                copy.ERROR_TEMPORARY_SERVICE.format(
                    platform=_platform_name(kind)),
                keyboards.retry_tasks_home_keyboard(task.token),
            )
        registry.finish(task.token, FAILED, note=f"0/{total}")
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
