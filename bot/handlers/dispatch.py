"""Universal link dispatcher (plan 1.1, redesign §3).

A pasted media link is detected and routed to the right downloader
regardless of which input mode the user is in — and even before they
ever sent /start (a copy of this handler runs in group 1, see main.py).
A supported link works from any state and is dispatched exactly once:
the URL filter runs before every state's TEXT handler, and this handler
never changes the input mode (a task is not a screen, spec §1.3).

Routing policy (spec §3.1):
- YouTube -> the SAME format picker from every entry point; the picker
  is tokenized so each link has its own picker and a new link never
  invalidates another.
- Spotify / Instagram / Pinterest / TikTok -> their flow immediately.
- Unsupported URLs never reach the LLM or a fake downloader: the
  input screens answer with the supported-platforms copy.
"""
import re
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes, filters

from utils import copy, keyboards, link_detect, tokens
from utils.constants.keyboards import YT_FMT_CALLBACK_PATTERN
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html

from bot.handlers.music.spotify import download as spotify_download
from bot.handlers.instagram import download as instagram_download
from bot.handlers.pinterest import download as pinterest_download
from bot.handlers.youtube import download as youtube_download, mp3 as youtube_mp3

# Init logger
logger = getLogger(__name__)

# How long a pasted YouTube link stays selectable after the picker is
# shown (spec §3.2: "the token identifies the exact URL").
YT_PICKER_TTL_SECONDS = 600.0


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

    Never changes the conversation state: downloads are tasks, not
    screens (spec §1.3, §2.3) — the user stays in whatever input mode
    they were in, so the mode's plain-text meaning is preserved.
    """
    assert update.effective_message is not None
    message = update.effective_message
    url = link_detect.first_url(message.text)
    # the filter guarantees a detectable URL
    assert url is not None
    platform = link_detect.detect_media(url)

    if platform == "youtube":
        await show_yt_picker(update, context, url)
        return None

    if platform == "tiktok":
        # yt-dlp handles TikTok like any other site. Watermark removal
        # is NOT supported today — never claim it is (spec §6.5).
        await youtube_download.run_video_download(
            update, context, url, kind="tiktok"
        )
        return None

    if platform == "spotify":
        await spotify_download.run_spotify_download(update, context, url)
        return None

    if platform == "instagram":
        await instagram_download.run_instagram_download(update, context, url)
        return None

    if platform == "pinterest":
        await pinterest_download.run_pinterest_download(update, context, url)
        return None

    # Unreachable while the filter gates this handler; kept so a platform
    # added to link_detect but not handled here fails loudly in logs.
    domain = escape_html(link_detect.url_domain(url))
    await message.reply_text(
        copy.ERROR_UNSUPPORTED_DOMAIN.format(domain=domain),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.home_keyboard,
    )
    return None


async def show_yt_picker(update: Update, context: MyContext, url: str) -> None:
    """The universal YouTube format picker (spec §3.2).

    One token per pasted link: every picker addresses its own URL,
    multiple pickers can be open at once, and a stale selection gets a
    toast instead of downloading a different link.
    """
    assert update.effective_message is not None
    store = tokens.get_token_store(context, "yt_pickers")
    token = store.put(url, YT_PICKER_TTL_SECONDS)
    await update.effective_message.reply_text(
        copy.YT_FORMAT_PROMPT,
        reply_markup=keyboards.yt_format_keyboard(token),
    )


async def on_yt_format(update: Update, context: MyContext):
    """Callback for the YouTube format picker (spec §3.2).

    `yt:fmt:<token>:<fmt>` — the token resolves the exact URL the picker
    was shown for. Answer immediately (no spinner), then start the
    task. The picker is single-use: re-clicking after the download
    started gets a stale toast instead of a duplicate task.
    """
    cq = update.callback_query
    assert cq is not None
    match = re.match(YT_FMT_CALLBACK_PATTERN, cq.data or "")
    assert match is not None  # the handler's pattern guarantees this
    token, fmt = match.group(1), match.group(2)

    store = tokens.get_token_store(context, "yt_pickers")
    url = store.get(token)
    if url is None:
        # stale button (expired, restarted, or already selected)
        await cq.answer(copy.TOAST_YT_STALE)
        return
    store.drop(token)  # single use

    await cq.answer()
    if fmt == "720":
        await youtube_download.run_video_download(update, context, url, max_height=720)
    elif fmt == "360":
        await youtube_download.run_video_download(update, context, url, max_height=360)
    else:  # mp3
        await youtube_mp3.run_mp3_download(update, context, url)


async def on_yt_cancel(update: Update, context: MyContext):
    """`yt:cancel:<token>` — dismiss just this picker (spec §3.2)."""
    cq = update.callback_query
    if cq is None:
        return None
    token = (cq.data or "").split(":")[-1]
    store = tokens.get_token_store(context, "yt_pickers")
    store.drop(token)
    await cq.answer()
    if cq.message is not None:
        try:
            await cq.message.edit_text("Cancelled.")
        except Exception:
            logger.debug("yt picker: could not edit picker message", exc_info=True)


async def on_yt_format_legacy(update: Update, context: MyContext):
    """Legacy no-token `yt:fmt:<fmt>` payload (cards from before the token
    rollout, spec §16). It cannot be resolved to a specific URL — it must
    NOT start a download of "the latest link" — so it gets a stale toast."""
    cq = update.callback_query
    if cq is None:
        return None
    await cq.answer(copy.TOAST_YT_STALE)
