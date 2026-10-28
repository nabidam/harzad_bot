"""Instagram download (UX redesign §6.2) — post, reel, story, profile.

Entry: an Instagram URL pasted anywhere (the universal dispatcher
runs first in every state) and `@username` on the Instagram input
screen (profile picture ONLY in that mode).

instagrapi's client is blocking (session login + API calls), so ALL
of it runs on the flow's worker thread — never on the event loop.
Each media item is delivered from its remote URL by the loop; there
is no local staging file.

Cancellation is best-effort and honest (spec §4.3): the tap stops
items that have not started yet. The 45 s metadata/fetch guard
(spec §4.4) DETACHES the wait — the worker thread cannot be killed,
so a late result is discarded and the copy never claims the backend
stopped.
"""
import re
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError

from instagrapi.exceptions import MediaNotFound, UnknownError, UserNotFound

from utils import copy, keyboards
from utils.constants import (
    IS_CLIPS,
    IS_FEED,
    IS_IGTV,
    MEDIA_ALBUM,
    MEDIA_IGTV,
    MEDIA_PHOTO,
    MEDIA_REEL,
    MEDIA_VIDEO,
    POST_SEGMENT,
    REEL_SEGMENT,
    STORY_SEGMENT,
)
from utils.constants.states import INSTAGRAM_INPUT_STATE
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html, react_message
from utils.instagram import get_user_client_instagram

from bot.handlers import common
from bot.handlers.youtube import flow

# Init logger
logger = getLogger(__name__)

# Spec §4.4 proposed guard for the Instagram metadata/fetch step
# (owner decision: the recommended default applies; production
# telemetry should validate it before it is treated as a measured
# fact — the same treatment as the Spotify resolve guard).
METADATA_TIMEOUT_SECONDS = 45

# instagram.com/<username> — the profile link shape (no post/reel/
# story segment).
_PROFILE_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")


def _media_item(kind: str, url, title: str) -> dict | None:
    if not url:
        return None
    return {"kind": kind, "url": str(url), "title": title, "caption": None}


def _items_from_media(media_info: dict) -> list[dict] | None:
    """Media items (remote URLs) from an instagrapi media_info dict.
    None when the media type is not downloadable. The post's caption
    rides on the LAST item — one concise caption (spec §6.2), no
    bot-id line (the caption is the post's own text, escaped)."""
    media_type = media_info["media_type"]
    product_type = media_info["product_type"]
    raw_caption = media_info.get("caption_text")
    caption = escape_html(raw_caption) if raw_caption else None

    items: list[dict] = []
    if media_type == MEDIA_ALBUM:
        for resource in media_info.get("resources") or []:
            if resource.get("video_url") is not None:
                items.append(_media_item("video", resource["video_url"], "Item"))
            else:
                items.append(_media_item("photo", resource.get("thumbnail_url"), "Item"))
    elif media_type == MEDIA_PHOTO:
        items.append(_media_item("photo", media_info.get("thumbnail_url"), "Photo"))
    elif (media_type == MEDIA_VIDEO and product_type == IS_FEED
            or media_type == MEDIA_IGTV and product_type == IS_IGTV
            or media_type == MEDIA_REEL and product_type == IS_CLIPS):
        items.append(_media_item("video", media_info.get("video_url"), "Video"))
    else:
        return None

    items = [item for item in items if item is not None]
    if not items:
        return None
    items[-1]["caption"] = caption
    return items


def _ig_worker(url: str, opts: dict, cancel_flag, push) -> dict:
    """Blocking worker (runs on a worker thread, spec §4.1/§6.2).

    Logs in, resolves the link, and builds the media item list — all
    network work happens here, never on the event loop. `@username`
    is a profile-picture request (input-screen shape). Emits the
    story phase line (status event), `resolved`, per-item
    item_start/item_done, and the sentinel that ends the flow in a
    finally block (flow contract).
    """
    outcome = {
        "error": None, "title": None, "total": None,
        "items": [], "failed": [],
    }
    try:
        client = None
        try:
            client = get_user_client_instagram()
        except Exception:
            logger.warning("Instagram client login failed", exc_info=True)
        if client is None:
            # session/cookie/server problem (spec §6.2): the service-
            # side failure card, and never a negative reaction
            outcome["error"] = "auth"
            return outcome

        target = url.strip()
        try:
            if target.startswith("@"):
                username = target.lstrip("@")
                user_data = client.user_info_by_username(username).model_dump()
                outcome["title"] = f"@{username}"
                items = [
                    _media_item("photo", user_data["profile_pic_url_hd"],
                                f"@{username}")
                ]
            elif STORY_SEGMENT in target:
                # the story phase line only when the worker actually
                # enters it (spec §6.2)
                push(("status", copy.INSTAGRAM_STORY))
                story_pk = client.story_pk_from_url(target)
                story_info = client.story_info(story_pk)
                outcome["title"] = "Story"
                items = [
                    _media_item("photo", story_info.thumbnail_url, "Story"),
                    _media_item("video", story_info.video_url, "Story"),
                ]
            elif POST_SEGMENT in target or REEL_SEGMENT in target:
                media_pk = client.media_pk_from_url(target)
                media_info = client.media_info(media_pk).model_dump()
                outcome["title"] = (media_info.get("caption_text") or "")[:60]
                items = _items_from_media(media_info)
                if items is None:
                    outcome["error"] = "invalid"
                    return outcome
            else:
                match = _PROFILE_RE.search(target)
                if match is None:
                    outcome["error"] = "invalid"
                    return outcome
                username = match.group(1)
                user_data = client.user_info_by_username(username).model_dump()
                outcome["title"] = f"@{username}"
                items = [
                    _media_item("photo", user_data["profile_pic_url_hd"],
                                f"@{username}")
                ]
        except MediaNotFound:
            outcome["error"] = "not_found"
            return outcome
        except UserNotFound:
            outcome["error"] = "user_not_found"
            return outcome
        except (UnknownError, ValueError):
            # a link the client cannot classify: inaccessible content,
            # not a service outage
            logger.warning("Instagram resolve failed for %r", target,
                           exc_info=True)
            outcome["error"] = "not_found"
            return outcome

        items = [item for item in items if item is not None]
        if not items:
            outcome["error"] = "not_found"
            return outcome
        outcome["items"] = items
        outcome["total"] = len(items)
        # Every item is known up front (remote URLs — no download
        # phase): push them all; the loop delivers them one by one,
        # checking the cancel flag before each send.
        push(("resolved", outcome["title"], len(items)))
        for index, item in enumerate(items, start=1):
            push(("item_start", index, len(items), item["title"]))
            push(("item_done", index, len(items), item))
    finally:
        # the flow's completion signal — pushed even when the worker
        # crashed (flow module docstring: the sentinel contract)
        push((flow.SENTINEL, outcome))
    return outcome


async def _deliver_ig(context, update, chat_id: int, item: dict) -> str:
    """Loop-side delivery of one Instagram media item from its remote
    URL (spec §6.2). protect_content stays on until the owner decides
    the forwarding policy (spec §15 open item)."""
    caption = item.get("caption")
    kwargs = {"chat_id": chat_id, "protect_content": True}
    if caption:
        kwargs.update(caption=caption, parse_mode=ParseMode.HTML)
    if item["kind"] == "video":
        await context.bot.send_chat_action(chat_id=chat_id,
                                           action=ChatAction.UPLOAD_VIDEO)
        try:
            await context.bot.send_video(video=item["url"], **kwargs)
        except TelegramError:
            # some URLs are only accepted as a document (legacy
            # fallback); a failure of the fallback propagates to the
            # flow's delivery-failure path
            await context.bot.send_document(document=item["url"], **kwargs)
    else:
        await context.bot.send_chat_action(chat_id=chat_id,
                                           action=ChatAction.UPLOAD_PHOTO)
        try:
            await context.bot.send_photo(photo=item["url"], **kwargs)
        except TelegramError:
            await context.bot.send_document(document=item["url"], **kwargs)
    return "sent"


def _ig_item_line(task, item_title: str | None, total: int | None) -> str:
    """The per-item line at each send boundary (spec §4.2 upload line;
    §6.2: `Sending item {current}/{total}` when a count is known)."""
    if total and total > 1:
        return copy.TASK_ITEM_UPLOADING.format(
            current=max(1, task.item_done + 1), total=total
        )
    subject = task.title or task.input
    return copy.TASK_UPLOADING.format(short_title=escape_html(subject[:60]))


def _error_card(err: str, token: str):
    """spec §6.2 error classes: session/cookie/server problem
    (temporary, [Retry][Tasks][Home]), inaccessible content and
    unknown profile (class 2, [Retry][Home]). Never a negative
    reaction for a server-side credential issue."""
    if err == "auth":
        return (
            copy.ERROR_INSTAGRAM_SESSION,
            keyboards.retry_tasks_home_keyboard(token),
        )
    if err == "user_not_found":
        return (
            copy.ERROR_INSTAGRAM_USER_NOT_FOUND,
            keyboards.retry_home_keyboard(token),
        )
    if err in ("not_found", "invalid", "extract", "empty"):
        return (
            copy.ERROR_INSTAGRAM_NOT_FOUND,
            keyboards.retry_home_keyboard(token),
        )
    return (copy.ERROR_UNEXPECTED, keyboards.retry_home_keyboard(token))


def _timeout_card(token: str):
    """The §4.4 45 s guard ends in the service-side failure copy
    (spec §6.2) — never a claim the backend stopped."""
    return (
        copy.ERROR_INSTAGRAM_SESSION,
        keyboards.retry_tasks_home_keyboard(token),
    )


async def run_instagram_download(update: Update, context: MyContext, text: str) -> None:
    """Core Instagram flow (spec §6.2), shared by the dispatcher and
    the Instagram input screen's `@username` branch.

    Undecorated core (like spotify's run_spotify_download): the
    callers carry their own chat action and DB work.
    """
    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    await context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                       action=ChatAction.TYPING)
    await flow.run_media_flow(
        update, context,
        url=text,
        kind="instagram",
        opts={},
        worker=_ig_worker,
        deliver=_deliver_ig,
        accepted=copy.INSTAGRAM_ACCEPTED,
        resolving=copy.INSTAGRAM_RESOLVING,
        slow=copy.INSTAGRAM_SLOW,
        item_line=_ig_item_line,
        cancel_label=copy.BTN_TASK_CANCEL,
        worker_timeout_seconds=METADATA_TIMEOUT_SECONDS,
        on_error=_error_card,
        on_timeout=_timeout_card,
    )


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: MyContext):
    """TEXT entry for the Instagram input screen (spec §2.2, §6.2).

    Supported media links never reach here (the universal dispatcher
    runs first for media links). `@username` is a profile-picture
    request ONLY in this input mode; plain non-link text gets the
    exact input guidance, never a fake downloader error.
    """
    assert update.message is not None
    text = (update.message.text or "").strip()

    if await common.precheck(update, context):
        return INSTAGRAM_INPUT_STATE

    if text.startswith("@"):
        await run_instagram_download(update, context, text)
        return INSTAGRAM_INPUT_STATE

    await common.reprompt(update, context, copy.INSTAGRAM_PROMPT)
    return INSTAGRAM_INPUT_STATE
