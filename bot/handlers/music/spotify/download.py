"""Spotify URL download (UX redesign §6.1) — track, album, playlist.

Entry is a Spotify URL pasted anywhere (the universal dispatcher) or
the Search Music input screen's URL branch; the result card's Download
button also lands here.

spotdl's ASYNC core is awaited on the bot's running loop (see
await_download_from_spotify for the incident this guards). Cancellation
is best-effort and honest (spec §4.3): the resolve and each track's
yt-dlp step run in worker threads that cannot be killed, so a cancel
stops items that have not started yet; an in-flight track that finishes
after the cancel is discarded, not sent, and the final count says so.

The 600 s (10 min) resolve guard (spec §4.4; owner decision 2026-10-31
raised it from the proposed 45 s on telemetry) carries a post-timeout
grace (see utils.executor): a process stop (SIGSTOP / cgroup freeze)
expires the local timer while the worker has already finished, and on
resume the finished result is honoured instead of discarded (incident
2026-10-28 21:39 — a 44 s healthy resolve + a 44 s process stop made
the guard fire on work that was already done).

The status message is one per task (spec §4.2): the accepted line at
t=0 with [⏹ Cancel download] [📥 Tasks], the per-track download line at
each item boundary, and the slow/still-working copy at the 30/60 s
measured thresholds of the download phase — never a fake progress bar.
"""
import asyncio
import validators
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError

from utils import copy, executor, keyboards, progress
from utils.constants.states import SEARCH_INPUT_STATE
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html, react_message
from utils.storage import cleanup_media
from utils.tasks import CANCELLED, DONE, FAILED, get_registry
from bot.handlers import common
from spotdl import Spotdl

# Init logger
logger = getLogger(__name__)

# Spec §4.4 guard for the Spotify URL resolve step. Owner decision
# 2026-10-31: 45 -> 600 s. Telemetry (2026-10-28..31) showed healthy
# resolves at 42.7-48.9 s plus a 123.5 s outlier: the Spotify API
# calls ride the box proxy, whose egress stalls for tens of seconds
# at a time, and spotipy sets no per-request timeout. 600 s lets a
# stalled egress ride out instead of bouncing the user to Retry; the
# status shows real elapsed time and the task stays cancellable, so
# the worst case (a dead egress) is a 10-minute honest verdict, not a
# fake progress bar.
RESOLVE_TIMEOUT_SECONDS = 600


def search_term(spotdl: Spotdl, term: str):
    songs = spotdl.search([term])

    return songs


def _submit_resolve(spotdl: Spotdl, url: str):
    """Submit the blocking spotdl.search to a detached worker thread
    (utils.executor: never joined on the loop thread) and return the
    worker, whose `done()` the timeout grace checks."""
    return executor.DetachedWorker(search_term, spotdl, url)


async def await_download_from_spotify(spotdl: Spotdl, song):
    """Download one song by awaiting spotdl's async core on the running loop.

    Never call spotdl's sync API (`spotdl.download`) from the bot: spotdl's
    Downloader.__init__ does asyncio.set_event_loop(its own fresh loop) in
    the main thread, and PTB's Application.__run picks that same loop up via
    asyncio.get_event_loop() and runs the whole bot on it. The sync API then
    calls loop.run_until_complete() on an already-running loop, raising
    RuntimeError("This event loop is already running"). Worse, the
    asyncio.gather() inside download_multiple_songs() schedules the download
    task on the running loop *before* run_until_complete() checks, so the
    song still downloads as an orphaned task: the mp3 lands on disk but the
    (already dead) handler never sends it. (Incident: 2026-10-27 10:03.)

    pool_download is spotdl's own per-song unit (semaphore +
    async_search_and_download), and async_search_and_download offloads every
    slow sync step (lyrics, yt-dlp search/download) to the default executor,
    so awaiting it on the bot loop does not block polling.
    """
    return await spotdl.downloader.pool_download(song)


def _song_title(song) -> str:
    """The track's title from a spotdl Song (attribute or raw json)."""
    name = getattr(song, "name", None)
    if not name:
        name = (song.json or {}).get("name")
    return name or ""


def _song_url(song):
    url = getattr(song, "url", None)
    if not url:
        url = (song.json or {}).get("url")
    return url


async def _deliver_spotify(context, chat_id: int, song, path: str) -> None:
    """Send one finished MP3 (spec §6.1), then drop the staging file.

    protect_content is off (owner decision 2026-10-31, spec §11.3):
    delivered media must stay downloadable and forwardable.
    """
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
    with open(path, "rb") as f:
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=f,
            thumbnail=getattr(song, "cover_url", None),
            title=_song_title(song),
            performer=getattr(song, "artist", None),
            duration=int((getattr(song, "duration", None) or 0) / 1000),
            protect_content=False,
        )
    # delivered to Telegram: the local file is staging only
    cleanup_media(path)


async def _await_resolve_or_cancel(
    spotdl: Spotdl, url: str, cancel_event: asyncio.Event,
    timeout_seconds: float | None = None,
):
    """Resolve the URL (spotdl.search in an executor) while racing the
    user's Cancel button and the spec §4.4 45 s resolve guard.

    Returns `(cancelled, timed_out, songs, failed)`. The resolve runs in
    a worker thread that cannot be killed: cancelling or timing out stops
    the WAIT and discards any late result — the copy never claims the
    backend stopped. A finished resolve wins a same-tick race (the work
    is done); it also wins a race lost only because the process was
    stopped across the guard line (the post-timeout grace, incident
    2026-10-28 21:39). An explicit Cancel beats the guard.
    """
    if timeout_seconds is None:
        timeout_seconds = RESOLVE_TIMEOUT_SECONDS
    worker = _submit_resolve(spotdl, url)
    resolve_fut = worker.fut
    cancel_task = asyncio.create_task(cancel_event.wait())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
    try:
        done, _pending = await asyncio.wait(
            {resolve_fut, cancel_task, timeout_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        # the whole handler was torn down (shutdown) — take all three
        for task in (resolve_fut, cancel_task, timeout_task):
            task.cancel()
        await asyncio.gather(
            resolve_fut, cancel_task, timeout_task, return_exceptions=True
        )
        raise

    resolve_done = resolve_fut.done()
    cancelled = not resolve_done and cancel_task in done
    timed_out = not resolve_done and not cancelled and timeout_task in done

    if timed_out:
        # The guard may have expired while the process was stopped
        # (SIGSTOP / cgroup freeze / host suspend): the worker had
        # already finished, and on resume the timer beat the delivery.
        # The grace lets a finished worker land — finished work is
        # never discarded at the guard line (incident 2026-10-28
        # 21:39: a 44 s healthy resolve + a 44 s process stop).
        if await worker.post_timeout_done():
            logger.info(
                "Spotify resolve for %r finished during the post-timeout "
                "grace (the process was likely stopped across the guard); "
                "honouring the result", url,
            )
            timed_out = False

    for task in (resolve_fut, cancel_task, timeout_task):
        if not task.done():
            task.cancel()
    await asyncio.gather(
        resolve_fut, cancel_task, timeout_task, return_exceptions=True
    )

    if cancelled:
        return True, False, None, False
    if timed_out:
        logger.warning(
            "Spotify resolve for %r timed out after %.0fs", url, timeout_seconds
        )
        return False, True, None, False
    try:
        return False, False, worker.result(), False
    except Exception:
        logger.warning("Spotify resolve failed for %r", url, exc_info=True)
        return False, False, None, True


async def run_spotify_download(update: Update, context: MyContext, url: str) -> None:
    """Core Spotify URL download (spec §6.1), shared by the dispatcher,
    the Search Music URL branch and the result card's Download button.

    Undecorated core (like spotify search's run_search): the callers
    carry their own chat action and DB work.
    """
    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None
    chat_id = update.effective_chat.id
    spotdl_instance = context.application.bot_data["spotdl_instance"]

    # Registered before anything runs: the Tasks view and
    # task:cancel:<token> work from the first second (spec §4.2).
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=update.effective_user.id,
        chat_id=chat_id,
        kind="spotify",
        input=url,
        retry_payload={"urls": [url]},
    )
    task.touch(phase="resolving")

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=copy.SPOTIFY_ACCEPTED,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.task_cancel_keyboard(
            task.token, cancel_label=copy.BTN_RETRY_DOWNLOAD
        ),
    )
    task.touch(status_message_id=status.message_id)

    async def _set_status(text: str, markup=None) -> None:
        # best-effort in-place edit (the user may have deleted it)
        try:
            await status.edit_text(
                text=text, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        except TelegramError:
            logger.warning("could not edit the status message", exc_info=True)

    # The tap's immediate feedback (spec §4.3): the status reads
    # "cancellation requested" the moment the user taps, until the
    # worker confirms at the next interruptible boundary.
    cancel_notified = False

    async def _on_cancel() -> None:
        nonlocal cancel_notified
        await task.cancel_event.wait()
        if cancel_notified or not task.is_active():
            return  # finished (or already reported) — never overwrite
        cancel_notified = True
        await _set_status(copy.TASK_CANCEL_REQUESTED,
                          keyboards.home_tasks_row_keyboard())

    cancel_watch = asyncio.create_task(_on_cancel())
    try:
        # --- resolve (45 s guard, spec §4.4) ------------------------------
        await context.bot.send_chat_action(chat_id=chat_id,
                                           action=ChatAction.TYPING)
        cancelled, timed_out, songs, failed = await _await_resolve_or_cancel(
            spotdl_instance, url, task.cancel_event
        )

        if cancelled:
            await _set_status(
                copy.TASK_CANCELLED.format(completed_count=0),
                keyboards.home_tasks_row_keyboard(),
            )
            registry.finish(task.token, CANCELLED, note="0/0")
            return
        if timed_out or failed:
            # The resolve thread may still be running; the copy says the
            # SERVICE is unavailable, never that we stopped it (spec
            # §4.4).
            await _set_status(
                copy.ERROR_TEMPORARY_SERVICE.format(platform="Spotify"),
                keyboards.retry_tasks_home_keyboard(task.token),
            )
            registry.finish(
                task.token, FAILED,
                note="resolve timeout" if timed_out else "resolve failed",
            )
            return
        if not songs:
            # the URL resolved to nothing: dead/private link, not a
            # service problem (spec §7.1 class 2)
            await _set_status(
                copy.ERROR_MEDIA_UNAVAILABLE,
                keyboards.retry_home_keyboard(task.token),
            )
            registry.finish(task.token, FAILED, note="not found")
            return

        # --- download phase: one status, per-track edits (spec §6.1) ----
        total = len(songs)
        # The stage callables read this at each threshold: the slow copy
        # names the track being worked on, the still-working copy its
        # title.
        state = {"title": "", "current": 0, "total": total}
        stages = [
            (progress.STAGE_SLOW,
             lambda e: copy.SPOTIFY_SLOW.format(
                 current=state["current"], total=state["total"])),
            (progress.STAGE_STILL_WORKING,
             lambda e: copy.SPOTIFY_VERY_SLOW.format(
                 title=escape_html(state["title"][:60]))),
        ]

        delivered = 0
        failed_items: list[tuple[str, str | None]] = []
        delivery_failed = False
        downloaded_paths: set[str] = set()
        handled_paths: set[str] = set()

        async with progress.status_ticker(
            context.bot, chat_id, status.message_id, stages=stages,
            parse_mode=ParseMode.HTML,
        ):
            for index, song in enumerate(songs, start=1):
                if task.cancel_event.is_set():
                    break  # cancel: stop before the next item (spec §4.3)
                title = _song_title(song)
                state.update(title=title, current=index)
                task.touch(item_done=index - 1, title=title,
                           phase="downloading")
                await _set_status(
                    copy.SPOTIFY_TRACK.format(
                        current=index, total=total,
                        title=escape_html(title[:60]), elapsed=task.elapsed(),
                    )
                )
                try:
                    result_song, path = await await_download_from_spotify(
                        spotdl_instance, song
                    )
                except Exception:
                    # one failed track never kills the rest (spec §6.1 /
                    # P0 checklist: failures are counted, never skipped)
                    logger.warning(
                        "Spotify track %d/%d (%s) failed", index, total,
                        title, exc_info=True,
                    )
                    failed_items.append((title, _song_url(song)))
                    task.touch(item_done=index,
                               item_failed=task.item_failed + 1)
                    continue
                if path is None:
                    failed_items.append((title, _song_url(song)))
                    task.touch(item_done=index,
                               item_failed=task.item_failed + 1)
                    continue
                path = str(path)
                downloaded_paths.add(path)
                if task.cancel_event.is_set():
                    # cancelled while this track was in flight: it is not
                    # sent, the staging file is dropped, the count stays
                    # true
                    task.touch(item_done=index)
                    break
                try:
                    await _deliver_spotify(context, chat_id, result_song,
                                           path)
                except TelegramError:
                    # a failed send is a failed item like a failed
                    # download: counted and retriable, never raised
                    # (the finalize step owns the terminal state, so
                    # the task can never be left running forever)
                    logger.warning("Spotify delivery failed for %r", title,
                                   exc_info=True)
                    failed_items.append((title, _song_url(song)))
                    delivery_failed = True
                    task.touch(item_done=index,
                               item_failed=task.item_failed + 1)
                    continue
                delivered += 1
                handled_paths.add(path)
                task.touch(item_done=index)

        # --- finalize: the status message becomes the final answer ------
        if task.cancel_event.is_set():
            if total > 1:
                text = copy.TASK_PLAYLIST_CANCELLED.format(
                    success_count=delivered, total_count=total
                )
            else:
                text = copy.TASK_CANCELLED.format(completed_count=delivered)
            await _set_status(text, keyboards.home_tasks_row_keyboard())
            registry.finish(task.token, CANCELLED, note=f"{delivered}/{total}")
        elif total == 1 and delivered:
            # no extra "all done" message (spec §4.2): the file is the
            # answer
            try:
                await status.delete()
            except TelegramError:
                logger.debug("status message already gone", exc_info=True)
            await react_message(update, context.bot, "👍")
            registry.finish(task.token, DONE, note="sent")
        elif total == 1:
            # the single track never went out: the status becomes the
            # failure card (spec §7.2) — a failed send is an internal
            # error (class 6), a failed download a temporary service
            # problem (class 3)
            if delivery_failed:
                await _set_status(copy.ERROR_UNEXPECTED,
                                  keyboards.retry_home_keyboard(task.token))
            else:
                await _set_status(
                    copy.ERROR_TEMPORARY_SERVICE.format(platform="Spotify"),
                    keyboards.retry_tasks_home_keyboard(task.token),
                )
            registry.finish(task.token, FAILED, note=f"0/{total}")
        else:
            failure_line = (
                copy.TASK_PLAYLIST_FAILED_LINE.format(
                    failed_count=len(failed_items))
                if failed_items else ""
            )
            text = copy.TASK_PLAYLIST_DONE.format(
                success_count=delivered, total_count=total,
                failure_line=failure_line,
            )
            await _set_status(
                text,
                keyboards.task_result_keyboard(
                    task.token, retry_failed=bool(failed_items)
                ),
            )
            registry.finish(
                task.token, DONE if delivered else FAILED,
                note=f"{delivered}/{total}",
            )

        # Retry replays only the failed tracks (spec §7.3) when their
        # URLs are known
        if failed_items:
            urls = [u for _title, u in failed_items if u]
            if urls:
                task.retry_payload = {"urls": urls}

        # staging files that never went out (discarded after a late
        # cancel, or a failed send) are deleted; delivered ones already
        # are
        for path in downloaded_paths - handled_paths:
            cleanup_media(path)
    finally:
        cancel_watch.cancel()
        await asyncio.gather(cancel_watch, return_exceptions=True)


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: MyContext):
    """TEXT entry for the Search Music input screen (spec §2.2, §6.1).

    A Spotify URL works here too (legacy entry): the universal
    dispatcher runs first for media links, so the URL branch below is a
    safety net. Plain text is a song query.
    """
    assert update.message is not None
    text = (update.message.text or "").strip()

    if await common.precheck(update, context):
        return SEARCH_INPUT_STATE

    message_is_url = bool(text) and bool(validators.url(text)) # type: ignore

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    if message_is_url:
        await run_spotify_download(update, context, text)
    else:
        # Plain text in Search Music is a song query (plan 1.2).
        # Lazy import: search.py imports this module at top level for the
        # card's Download button — a top-level import here would cycle.
        from bot.handlers.music.spotify import search as spotify_search
        await spotify_search.run_search(update, context, text)

    return SEARCH_INPUT_STATE
