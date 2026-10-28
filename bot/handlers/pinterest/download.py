"""Pinterest download (UX redesign §6.4) — pin video links.

Entry: a Pinterest URL pasted anywhere (the universal dispatcher runs
first in every state) or after choosing Pinterest; the input screen
itself lives in main.py.

The download REUSES the shared video engine (spec §6.4: "reuse the
video-engine timeout guard until Pinterest-specific latency has been
measured"): one yt-dlp worker thread (resolve + download to staging),
one status message on the Pinterest wait copy, honest cancellation at
item boundaries, the >50 MB direct-link card (spec §6.6), and the
single-item failure card (spec §7.2).
"""
from logging import getLogger

from telegram import Update

from configurations.settings import DOWNLOAD_VIDEO_PATH
from utils import copy, keyboards
from utils.custom_context import MyContext

from bot.handlers.youtube import download as yt_download
from bot.handlers.youtube import flow

# Init logger
logger = getLogger(__name__)


def _pinterest_opts() -> dict:
    return {
        'format': 'bestvideo[ext=mp4]+bestaudio/best',
        'outtmpl': DOWNLOAD_VIDEO_PATH + '%(title)s.%(ext)s',
    }


def _error_card(err: str, token: str):
    """spec §6.4: an unrecognizable link gets the invalid-Pinterest
    card with [Retry][Home]. An unavailable media/service is covered
    by the engine's single-item failure card (the service-side error
    in §7) — not here."""
    if err in ("extract", "empty"):
        return (
            copy.ERROR_INVALID_PINTEREST,
            keyboards.retry_home_keyboard(token),
        )
    return (copy.ERROR_UNEXPECTED, keyboards.retry_home_keyboard(token))


async def run_pinterest_download(update: Update, context: MyContext, text: str) -> None:
    """Core Pinterest flow (spec §6.4), shared by the dispatcher and
    the Pinterest input screen.

    Undecorated core (like the YouTube flow's run_video_download):
    the callers carry their own chat action and DB work.
    """
    await flow.run_media_flow(
        update, context,
        url=text,
        kind="pinterest",
        opts=_pinterest_opts(),
        worker=yt_download.video_worker,
        deliver=yt_download.deliver_video,
        accepted=copy.PINTEREST_ACCEPTED,
        resolving=copy.PINTEREST_RESOLVING,
        slow=copy.TASK_SLOW,
        on_error=_error_card,
    )
