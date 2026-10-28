"""YouTube MP3 download (UX redesign §6.3) — the picker's Audio option.

Same engine as the video flow (flow.run_media_flow): one task, one
status message, honest cancellation at item boundaries. Audio is
extracted to MP3 192k by an FFmpeg postprocessor.
"""
import os
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction
from pydub import AudioSegment
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from configurations.settings import DOWNLOAD_MP3_PATH, YT_COOKIE_FILE
from utils import copy, keyboards
from utils.constants import PLAYLIST
from utils.constants.messages import BOT_ID, MEDIA_CAPTION
from utils.helpers import escape_html, prepare_link
from utils.storage import cleanup_media

from bot.handlers.youtube import flow

# Init logger
logger = getLogger(__name__)


def _mp3_opts() -> dict:
    return {
        'format': 'bestaudio/best',
        'outtmpl': DOWNLOAD_MP3_PATH + '%(title)s.%(ext)s',
        'restrictfilenames': True,
        'cookiefile': YT_COOKIE_FILE,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }


def _mp3_worker(url: str, opts: dict, cancel_flag, push) -> dict:
    """Blocking worker (runs on a worker thread): resolve, then extract
    audio per entry. Duration is read from the finished MP3 (pydub is
    blocking, so it runs here, not on the event loop). Pushes the
    sentinel that ends the flow in a finally block (flow contract)."""
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
                    audio = AudioSegment.from_file(filename, format="mp3")
                    item_result = {
                        "path": filename,
                        "title": item_title,
                        "url": item.get("url") or item.get("webpage_url"),
                        "duration": int(len(audio) / 1000),
                        "size_mb": round(os.path.getsize(filename) / (1024 * 1024), 2),
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


async def _deliver_audio(context, update, chat_id: int, item: dict) -> str:
    """Loop-side delivery of one extracted MP3 (spec §6.3/§6.6)."""
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

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
    with open(item["path"], "rb") as f:
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=f,
            duration=item["duration"],
            title=item["title"],
            thumbnail=item["thumbnail_url"],
            caption=MEDIA_CAPTION.format(
                caption=escape_html(item["title"]), bot_id=BOT_ID
            ),
            parse_mode="HTML",
            protect_content=True,
        )
    # delivered to Telegram: the local file is staging only
    cleanup_media(item["path"])
    return "sent"


async def run_mp3_download(update: Update, context, url: str) -> None:
    """The YouTube MP3 flow (undecorated core; the picker caller carries
    its own chat action and DB work)."""
    await flow.run_media_flow(
        update, context,
        url=url,
        kind="youtube_mp3",
        opts=_mp3_opts(),
        worker=_mp3_worker,
        deliver=_deliver_audio,
    )
