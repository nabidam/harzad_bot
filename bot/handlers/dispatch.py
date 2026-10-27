"""Universal link dispatcher (plan 1.1): zero-friction ingestion.

A pasted media link is detected and routed to the right downloader
regardless of which conversation state the user is in — and even before
they ever sent /start: a copy of this handler runs in group 1 (see
main.py), which only fires when the conversation in group 0 has no state
for the user. The menu flow keeps working exactly as before; this is an
additional entry path.

YouTube links get an instant format selector instead of one blind
download: [🎬 Video (720p)] [🎬 Video (360p)] [🎵 Audio MP3].
"""
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes, filters

from utils import keyboards, link_detect
from utils.constants.keyboards import (
    CB_YT_FMT_360,
    CB_YT_FMT_720,
    CB_YT_FMT_MP3,
    YT_FMT_CALLBACK_PATTERN,
)
from utils.constants.messages import UNSUPPORTED_LINK, YT_FORMAT_PROMPT
from utils.constants.states import (
    INSTAGRAM_DOWNLOAD_STATE,
    MUSIC_SPOTIFY_STATE,
    PINTEREST_STATE,
)
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html

from bot.handlers.music.spotify import download as spotify_download
from bot.handlers.instagram import download as instagram_download
from bot.handlers.pinterest import download as pinterest_download
from bot.handlers.youtube import download as youtube_download, mp3 as youtube_mp3

# Init logger
logger = getLogger(__name__)


class MediaUrlFilter(filters.MessageFilter):
    """True when the message carries a URL of a platform we download.

    Messages without a link (or with a foreign one, e.g. a google search)
    fall through to whatever the state's own TEXT handler does.

    Must subclass MessageFilter (not plain BaseFilter): the framework
    calls the filter's `check_update`, and BaseFilter.check_update only
    tests "is this a message update at all" — it never invokes
    `filter()`. Subclassing BaseFilter made every text message (even
    /start) pass this filter (live incident 2026-10-27 12:58).
    """

    def filter(self, message) -> bool:
        url = link_detect.first_url(message.text)
        return bool(url is not None and link_detect.detect_media(url) is not None)


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handle_link(update: Update, context: MyContext):
    """Route a media URL to its downloader.

    Returns a conversation state (in-conversation runs) so the user ends
    up where the menu flow would have left them; ignored for group-1
    (never-started) users.
    """
    assert update.effective_message is not None
    message = update.effective_message
    url = link_detect.first_url(message.text)
    # the filter guarantees a detectable URL
    assert url is not None
    platform = link_detect.detect_media(url)

    if platform == "youtube":
        context.user_data["yt_url"] = url
        await message.reply_text(
            YT_FORMAT_PROMPT,
            reply_markup=keyboards.youtube_format_keyboard,
        )
        return None

    if platform == "tiktok":
        # yt-dlp handles TikTok like any other site; the watermark-free
        # polish (plan 2.1) lands later.
        await youtube_download.run_video_download(update, context, url)
        return None

    if platform == "spotify":
        await spotify_download.run_spotify_download(update, context, url)
        return MUSIC_SPOTIFY_STATE

    if platform == "instagram":
        await instagram_download.run_instagram_download(update, context, url)
        return INSTAGRAM_DOWNLOAD_STATE

    if platform == "pinterest":
        await pinterest_download.run_pinterest_download(update, context, url)
        return PINTEREST_STATE

    # Unreachable while the filter gates this handler; kept so a platform
    # added to link_detect but not handled here fails loudly in logs.
    domain = escape_html(link_detect.url_domain(url))
    await message.reply_text(
        UNSUPPORTED_LINK.format(domain=domain),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.start_keyboard_rm,
    )
    return None


async def on_yt_format(update: Update, context: MyContext):
    """Callback for the YouTube format selector (plan 1.1).

    Registered in every conversation state (bot/conversations/main.py)
    and, for users without an active conversation, in group 1 (main.py).
    The URL was stashed in user_data when the selector was shown.
    """
    cq = update.callback_query
    assert cq is not None

    url = context.user_data.pop("yt_url", None)
    if url is None:
        # stale button (newer link pasted meanwhile, or process restart)
        await cq.answer("Link expired — paste it again")
        return

    await cq.answer()
    if cq.data == CB_YT_FMT_720:
        await youtube_download.run_video_download(update, context, url, max_height=720)
    elif cq.data == CB_YT_FMT_360:
        await youtube_download.run_video_download(update, context, url, max_height=360)
    else:  # CB_YT_FMT_MP3
        await youtube_mp3.run_mp3_download(update, context, url)
