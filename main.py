# coding: utf-8
import asyncio
import fcntl
import io
import os
import signal
import sys
from importlib import import_module
from datetime import datetime
from logging import getLogger

from telegram import Update, BotCommand, InputFile, InputProfilePhotoStatic, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    Application,
    InlineQueryHandler,
    MessageHandler,
    PicklePersistence,
)
from telegram.request import BaseRequest, HTTPXRequest
from PIL import Image
from bot.conversations.main import main_conversation_handler
from bot.handlers import callbacks, dispatch
from bot.handlers import inline as inline_query
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID, DOWNLOAD_MP3_PATH, YT_COOKIE_FILE, YT_PROXY, SCRIPT_DIR
from utils import copy, keyboards
from utils.ack import answer_callback
from web.app import start_server

import configurations.settings as settings
from connectors.spotdl import create_spotdl_instance
from utils.custom_context import MyContext
from utils.helpers import create_requirement_folders
from utils.logger import init_logger
from utils import storage
from spotdl import SpotifyClient, Spotdl

logger = getLogger(__name__)

# Egress rides a flaky local proxy. PTB applies the *normal* read timeout
# to file uploads too: the clock runs from when the body is fully written,
# while Telegram only answers after the proxy has relayed the whole upload.
# With a 50 s read timeout every few-MB sendAudio "failed" client-side while
# the track was still in flight — it arrived in the chat minutes later, i.e.
# right when the user sent the NEXT link, so the bot looked like it answered
# the new link with the previous track (incident 2026-11-17 13:14). Uploads
# therefore get their own long timeout; small JSON calls keep 50 s.
UPLOAD_TIMEOUT = 600


class UploadAwareRequest(HTTPXRequest):
    """HTTPXRequest with a dedicated read/write timeout for file uploads.

    Only multipart requests (requests carrying files) get UPLOAD_TIMEOUT;
    everything else keeps the instance's normal timeouts. The override
    applies when the caller did not pass an explicit timeout (PTB's
    DEFAULT_NONE sentinel).
    """

    __slots__ = ()

    async def do_request(self, url, method, request_data=None,
                         read_timeout=BaseRequest.DEFAULT_NONE,
                         write_timeout=BaseRequest.DEFAULT_NONE,
                         connect_timeout=BaseRequest.DEFAULT_NONE,
                         pool_timeout=BaseRequest.DEFAULT_NONE):
        if request_data is not None and request_data.multipart_data:
            if read_timeout is BaseRequest.DEFAULT_NONE:
                read_timeout = UPLOAD_TIMEOUT
            if write_timeout is BaseRequest.DEFAULT_NONE:
                write_timeout = UPLOAD_TIMEOUT
        return await super().do_request(
            url, method, request_data, read_timeout, write_timeout,
            connect_timeout, pool_timeout,
        )


async def post_init(application: Application):
    """Configure the bot's public profile once the application is up.

    PTB does NOT run post_init inside its network retry loop — only the
    bootstrap `initialize` is wrapped (see Application.__run). A transient
    proxy failure here used to kill the whole process (2026-10-27 10:49:
    setMyShortDescription hit the 50 s read timeout 50 s after start).
    Everything below is guarded: these are profile cosmetics, the next
    boot sets them again.
    """
    bot = application.bot
    try:
        # Commands and profile copy per the redesign spec §13.3.
        await bot.set_my_commands([
            BotCommand(command="start", description="Open Home"),
            BotCommand(command="help", description="How to use Harzad"),
            BotCommand(command="tasks", description="View active tasks"),
            BotCommand(command="cancel", description="Cancel a running task"),
        ])
        await bot.set_my_description(copy.BOT_DESCRIPTION)
        await bot.set_my_short_description(copy.BOT_SHORT_DESCRIPTION)
    except Exception:
        logger.warning(
            "post_init: profile setup failed; retried on next boot", exc_info=True
        )
    await set_profile_photo(bot)
    await start_mini_app(application)

    # Plan 1.5: background garbage collection for the download folders
    # (files older than 2h are removed; runs hourly). create_task keeps a
    # reference so the task is cancelled with the application on shutdown.
    application.create_task(storage.gc_loop())


async def start_mini_app(application: Application) -> None:
    """Start the Mini App web server and (re)set the chat menu button.

    The server always starts (loopback-only, harmless when idle); the menu
    button is only set when MINIAPP_URL is configured, because the tunnel
    domain can change between restarts and is re-read from env at every boot.
    A failure never blocks startup — the bot keeps running without the app.
    """
    try:
        await start_server(
            application.bot,
            settings.WEB_HOST,
            settings.WEB_PORT,
            settings.MINIAPP_URL,
        )
    except Exception:
        logger.warning("Could not start the Mini App web server", exc_info=True)
        return
    if settings.MINIAPP_URL:
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Open Harzad",
                    web_app=WebAppInfo(url=settings.MINIAPP_URL),
                )
            )
            logger.info("Chat menu button → %s", settings.MINIAPP_URL)
        except Exception:
            logger.warning("Could not set the chat menu button", exc_info=True)
    else:
        logger.info("MINIAPP_URL not set — web server up, menu button disabled")


async def set_profile_photo(bot) -> None:
    """Set the bot's public profile photo from assets/logo.png (Bot API 9.4).

    Telegram requires a square JPEG of at most 512x512 px and 5 MB, so the
    1254x1254 source PNG is downscaled in memory. Idempotent — safe to run
    at every boot, and a failure never blocks startup.
    """
    logo_path = os.path.join(SCRIPT_DIR, "assets", "logo.png")
    try:
        img = Image.open(logo_path).convert("RGB")
        img = img.resize((512, 512), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        await bot.set_my_profile_photo(
            InputProfilePhotoStatic(
                # attach=True: the nested photo field must be an attach://
                # reference to the multipart file part, not a raw upload.
                InputFile(buf.getvalue(), filename="profile.jpg", attach=True)
            )
        )
        logger.info("Profile photo set from %s", logo_path)
    except Exception:
        logger.warning("Could not set profile photo from %s", logo_path, exc_info=True)


async def handle_stray_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group-1 catch-all for inline-button clicks with no active
    conversation.

    Inline buttons stay clickable forever after they are sent. A user
    who never sent /start (e.g. they only used the universal link
    dispatcher) has no conversation state, so the group-0 conversation
    handler ignores their clicks. The shared chain's patternless
    navigator already answers every callback it doesn't recognize, so
    this is a last-resort safety net: answer and move on.
    """
    cq = update.callback_query
    if cq is None:
        return
    await answer_callback(cq)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Catch unhandled handler errors.

    Without this, any exception (e.g. a DB outage) silently drops the update
    and the user never hears back. Log the full traceback, then tell the user.
    """
    # update is None for errors raised by the polling/updater layer itself
    # (e.g. 409 Conflict while a second bot instance is running) — those are
    # not tied to a user message, so only log them, without the misleading
    # "handling update" label.
    where = (
        "while handling update"
        if isinstance(update, Update)
        else "in the polling layer (no update)"
    )
    logger.error("Unhandled exception %s", where, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            # Spec §7: the unexpected-error class is a service-side
            # fallback — no blame, no misleading reaction, a Home exit.
            # No Retry button: an unhandled exception has no safe replay
            # payload (spec §7.3).
            await update.effective_message.reply_text(
                copy.ERROR_UNEXPECTED,
                parse_mode="HTML",
                reply_markup=keyboards.home_keyboard,
            )
        except Exception:
            logger.debug("Could not notify the user about the error", exc_info=True)


def _conversation_key(conv, update):
    """Mirror of ConversationHandler._get_key for the group-1 guard.

    Built from the handler's own per_* flags (this app uses the defaults:
    per_chat + per_user). Returns None when the key cannot be built
    (e.g. callbacks from inline results carry no chat).
    """
    key = []
    chat, user = update.effective_chat, update.effective_user
    if getattr(conv, "per_chat", True):
        if chat is None:
            return None
        key.append(chat.id)
    if getattr(conv, "per_user", True):
        if user is None:
            return None
        key.append(user.id)
    if getattr(conv, "per_message", False):
        cq = update.callback_query
        if cq is None:
            return None
        key.append(cq.inline_message_id or (cq.message.message_id if cq.message else None))
    return tuple(key) if key else None


def _group1_guard(conv):
    """Wrap a group-1 handler so it only runs for users WITHOUT an active
    conversation.

    PTB evaluates every group for every update ("0 or 1 handler per
    group", not "stop after the first group" — see Application
    add_handler docs). The conversation carries its own copies of the
    dispatcher's handlers, so without this guard a link or button
    handled inside the conversation would be handled a second time in
    group 1: double downloads, spurious "expired" toasts, duplicate
    answerCallbackQuery/editMessageText calls (live incident
    2026-10-27 12:58 — one nav click answered + edited twice).
    """
    def wrap(handler):
        async def guarded(update: Update, context):
            key = _conversation_key(conv, update)
            # _conversations is PTB-internal (no public accessor in v22);
            # the dict is updated by the group-0 run before group 1 sees
            # the update, so an entry here means the conversation already
            # handled (or is handling) this user.
            if key is not None and key in getattr(conv, "_conversations", {}):
                return None
            return await handler(update, context)

        guarded._harzad_guarded = handler
        return guarded

    return wrap


def register_handlers(application: Application) -> None:
    """All handler registration, in group order (plan 1.1 wiring,
    redesign §16).

    Kept in a function so tests can build a real Application and verify
    the tree without booting the bot.
    """
    conv = main_conversation_handler()
    application.add_handler(conv)
    guard = _group1_guard(conv)
    # Inline query mode (plan 1.3): @harzad_bot <song> in any chat.
    # A separate update type — the group-0 conversation never sees it.
    application.add_handler(InlineQueryHandler(inline_query.handler))
    # Group 1: fallback for users with no active conversation (never sent
    # /start) — a bare pasted link or a stray card button must still work.
    # Every handler below is guard-wrapped: when the user DOES have an
    # active conversation, the group-0 conversation's own copy of the
    # handler owns the update (PTB still evaluates group 1 — see
    # _group1_guard). The shared callback chain (patterns preserved)
    # covers the tokenized actions, the navigator and the stateless
    # catch-all in one ordered list.
    application.add_handler(
        MessageHandler(dispatch.MediaUrlFilter(), guard(dispatch.handle_link)), group=1
    )
    for handler in callbacks.shared_callback_handlers(guard):
        application.add_handler(handler, group=1)
    application.add_handler(CallbackQueryHandler(guard(handle_stray_callback)), group=1)
    application.add_error_handler(on_error)


if __name__ == "__main__":
    now = datetime.now()
    strtime = now.strftime("%Y_%m_%d_%H_%M_%S")
    init_logger(f"logs/{settings.NAME}_{strtime}.log")

    # Single-instance guard. Two processes polling the same token cause a
    # 409 Conflict storm, and two fresh pollers can BOTH receive the same
    # update (in-flight long-polls with overlapping offsets), so one user
    # action gets handled twice — two downloads, two error messages, etc.
    # (Incident: 2026-10-27, second instance started by hand.)
    # First process to hold the lock wins; later starts exit.
    lock_path = os.path.join(SCRIPT_DIR, "run", f"{settings.NAME}.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.lseek(lock_fd, 0, os.SEEK_SET)
        holder = os.read(lock_fd, 128).decode("utf-8", "replace").strip()
        logger.error(
            "Another %s instance is already running (%s). "
            "Refusing to start a second listener. Stop the first one first.",
            settings.NAME, holder or "unknown holder",
        )
        sys.exit(1)
    os.ftruncate(lock_fd, 0)
    os.write(lock_fd, f"pid {os.getpid()}, started {strtime}".encode())
    # Keep lock_fd referenced at module level for the process lifetime —
    # the flock is released as soon as the fd is closed (a GC'd local would
    # drop the lock and let a second instance start).
    _BOT_LOCK_FD = lock_fd

    create_requirement_folders()
    # SpotifyClient.init(
    #                 client_id=client_id,
    #                 client_secret=client_secret,
    #                 user_auth=user_auth,
    #                 cache_path=cache_path,
    #                 no_cache=no_cache,
    #                 headless=headless,
    #             )
    # SpotifyClient.init(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
    
    downloader_settings = {
        "simple_tui": True,
        "yt_dlp_args": f"--cookies {YT_COOKIE_FILE} --ignore-errors --impersonate chrome --force-ipv4 --proxy {YT_PROXY}",
        "output": DOWNLOAD_MP3_PATH,
        #"lyrics_providers": ["genius", "musixmatch"],
        "parallel_downloads": 1
    }
    
    spotdl_instance = Spotdl(
        client_id=SPOTIFY_CLIENT_ID, 
        client_secret=SPOTIFY_CLIENT_SECRET,
        downloader_settings=downloader_settings
    )

    # spotdl's Downloader.__init__ called asyncio.set_event_loop(its own
    # fresh loop) in this thread. PTB's Application.__run picks the
    # main thread's loop up via asyncio.get_event_loop() and runs the whole
    # bot on it — which is what made spotdl's sync download API
    # (run_until_complete on "its" loop) crash with "This event loop is
    # already running" and orphan the download task (2026-10-27 10:03).
    # Give this thread its own loop again so the bot loop and spotdl's loop
    # are separate objects. The spotify handler drives spotdl only through
    # its async API (downloader.pool_download) on the bot loop.
    asyncio.set_event_loop(asyncio.new_event_loop())

    # spotdl = create_spotdl_instance()

    # persistence = PicklePersistence(filepath="conversation states")
    
    context_types = ContextTypes(context=MyContext)

    application = (ApplicationBuilder()
                   .token(settings.TOKEN)
                #    .context_types(context_types)
                   .post_init(post_init)
                   # egress goes through a flaky proxy: tolerate slow/failed
                   # bootstraps instead of dying on the first attempt.
                   # Both requests are explicit instances — setting
                   # .request() disables the individual timeout setters,
                   # and the polling request keeps its own (long-poll)
                   # read timeout. File uploads ride UploadAwareRequest's
                   # long timeout (UPLOAD_TIMEOUT, see above).
                   .request(UploadAwareRequest(
                       connect_timeout=30, read_timeout=50, write_timeout=50,
                   ))
                   .get_updates_request(HTTPXRequest(
                       connection_pool_size=1, read_timeout=50,
                   ))
                #    .persistence(persistence)
                   .build())

    
    application.bot_data["spotdl_instance"] = spotdl_instance

    register_handlers(application)

    # bootstrap_retries: -1 = keep retrying while the (flaky) proxy is down,
    # instead of dying on the first failed getMe
    application.run_polling(bootstrap_retries=-1)
