"""YouTube video download (UX redesign §6.3) — and the TikTok path.

Entry is the universal format picker (spec §3.2) from any input mode, or
a TikTok link via the dispatcher (TikTok reuses this video engine; there
is no format picker for it and no watermark-removal claim, spec §6.5).

The blocking yt-dlp work runs on a worker thread (flow.run_media_flow):
resolve the link, then download — a single file, or playlist entries one
by one via process_info(item, download=True) so each item is an
interruptible boundary for cancellation and a failure in one item never
kills the rest.
"""
import os
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from configurations.settings import DOWNLOAD_VIDEO_PATH, YT_COOKIE_FILE
from utils import copy, keyboards
from utils.constants import PLAYLIST
from utils.constants.messages import BOT_ID, MEDIA_CAPTION
from utils.helpers import escape_html, prepare_cover, prepare_link
from utils.storage import cleanup_media
from utils.youtube_dl import video_opts

from bot.handlers.youtube import flow

# Init logger
logger = getLogger(__name__)


def _video_worker(url: str, opts: dict, cancel_flag, push) -> dict:
    """Blocking worker (runs on a worker thread, spec §4.1/§6.3).

    Resolves the link, then downloads every entry one by one. Emits
    `resolved` / `item_start` / `item_done` / `item_failed` events on
    the loop, and the sentinel that ends the flow (finally block). A
    cancel requested mid-item takes effect after the current file
    (yt-dlp cannot be interrupted inside a download) — the flow
    reports the honest "requested" state until then.
    """
    from mutagen import File as MutagenFile

    outcome = {
        "error": None, "title": None, "total": None,
        "items": [], "failed": [],
    }
    try:
        with YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except DownloadError:
                logger.warning("yt-dlp extract failed for %s", url, exc_info=True)
                outcome["error"] = "extract"
                return outcome

            is_playlist = info.get("_type") == PLAYLIST
            entries = (
                [e for e in (info.get("entries") or []) if e is not None]
                if is_playlist else [info]
            )
            outcome["title"] = info.get("title") or url
            outcome["total"] = len(entries)
            push(("resolved", outcome["title"], len(entries)))
            if not entries:
                outcome["error"] = "empty"
                return outcome

            for index, item in enumerate(entries, start=1):
                if cancel_flag.is_set():
                    break  # cancel: stop before the next item (spec §4.3)
                item_title = item.get("title") or f"item {index}"
                push(("item_start", index, len(entries), item_title))
                try:
                    ydl.process_info(item, download=True)
                    filename = ydl.prepare_filename(item)
                    file_size = os.path.getsize(filename)
                    item_result = {
                        "path": filename,
                        "title": item_title,
                        "url": item.get("url") or item.get("webpage_url"),
                        "duration": int(MutagenFile(filename).info.length),
                        "size_mb": round(file_size / (1024 * 1024), 2),
                        "thumbnail_url": item.get("thumbnail"),
                    }
                    outcome["items"].append(item_result)
                    push(("item_done", index, len(entries), item_result))
                except DownloadError:
                    logger.warning(
                        "yt-dlp failed on item %d/%d (%s)",
                        index, len(entries), item_title, exc_info=True,
                    )
                    outcome["failed"].append(
                        (item_title, item.get("url") or item.get("webpage_url"))
                    )
                    push(("item_failed", index, len(entries), item_title))
    finally:
        # the flow's completion signal — pushed even when the worker
        # crashed (flow module docstring: the sentinel contract)
        push((flow.SENTINEL, outcome))
    return outcome


async def _deliver_video(context, update, chat_id: int, item: dict) -> str:
    """Loop-side delivery of one downloaded item (spec §6.3/§6.6).

    <=50 MB: the video itself (title, cover, duration, streaming), then
    the staging file is deleted. >50 MB: the direct-link card — the
    file is kept because the link points at it.
    """
    if item["size_mb"] >= flow.MAX_SEND_MB:
        link = prepare_link(item["path"])
        text = (
            copy.ERROR_TOO_LARGE + "\n"
            + copy.ERROR_TOO_LARGE_NO_KNOWN_EXPIRY
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboards.too_large_keyboard(link),
        )
        return "linked"

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
    with open(item["path"], "rb") as f:
        # distinct InputFile per field: each needs its own multipart
        # part; the remote URL is webp, which Telegram's thumbnail
        # field rejects
        thumbnail_file = prepare_cover(item["path"])
        cover_file = prepare_cover(item["path"])
        await context.bot.send_video(
            chat_id=chat_id,
            video=f,
            duration=item["duration"],
            thumbnail=thumbnail_file if thumbnail_file is not None else item["thumbnail_url"],
            cover=cover_file,
            caption=MEDIA_CAPTION.format(
                caption=escape_html(item["title"]), bot_id=BOT_ID
            ),
            parse_mode="HTML",
            protect_content=True,
            supports_streaming=True,
        )
    # delivered to Telegram: the local file is staging only
    cleanup_media(item["path"])
    return "sent"


async def run_video_download(
    update: Update, context, url: str, *, max_height: int = 720,
    kind: str = "youtube_video",
) -> None:
    """The YouTube/TikTok video flow (undecorated core, like
    spotify run_search — the picker/dispatcher callers carry their own
    chat action and DB work).

    `kind` names the task in the Tasks view ("youtube_video", or
    "tiktok" for the dispatcher's watermark-free video path).
    """
    await flow.run_media_flow(
        update, context,
        url=url,
        kind=kind,
        opts=video_opts(max_height),
        worker=_video_worker,
        deliver=_deliver_video,
        max_height=max_height,
    )
