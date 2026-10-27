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
from PIL import Image
from bot.conversations.main import main_conversation_handler
from bot.handlers import dispatch
from bot.handlers import inline as inline_query
from bot.handlers.music.spotify import search as spotify_search
from bot.navigation import NAVIGATOR, navigate
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID, DOWNLOAD_MP3_PATH, YT_COOKIE_FILE, YT_PROXY, SCRIPT_DIR
from utils.constants.keyboards import SONG_ACTION_CALLBACK_PATTERN, YT_FMT_CALLBACK_PATTERN
from utils.constants.messages import SOMETHING_WENT_WRONG
from web.app import start_server

import configurations.settings as settings
from connectors.spotdl import create_spotdl_instance
from utils.custom_context import MyContext
from utils.helpers import create_requirement_folders, react_message
from utils.logger import init_logger
from utils import storage
from spotdl import SpotifyClient, Spotdl

logger = getLogger(__name__)


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
        await bot.set_my_commands([
            BotCommand(command="start", description="Open the menu"),
            BotCommand(command="help", description="How to use the bot"),
        ])
        await bot.set_my_description(
            "Harzad downloads media from Instagram, Spotify, Pinterest and YouTube, "
            "and can chat or generate images with AI. Send /start to begin."
        )
        await bot.set_my_short_description("Media downloader + AI 🎧🎞🤖")
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
    handler ignores their clicks. For nav buttons we run the shared
    navigator — that drops them into the menu with no /start; anything
    else is just answered so the client's button spinner doesn't hang.
    """
    cq = update.callback_query
    if cq is None:
        return
    if (cq.data or "") in NAVIGATOR:
        await navigate(update, context)
        return
    await cq.answer()


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
            await react_message(update, context.bot, "👎")
            await update.effective_message.reply_text(SOMETHING_WENT_WRONG)
        except Exception:
            logger.debug("Could not notify the user about the error", exc_info=True)


def register_handlers(application: Application) -> None:
    """All handler registration, in group order (plan 1.1 wiring).

    Kept in a function so tests can build a real Application and verify
    the tree without booting the bot.
    """
    application.add_handler(main_conversation_handler())
    # Inline query mode (plan 1.3): @harzad_bot <song> in any chat.
    # A separate update type — the group-0 conversation never sees it.
    application.add_handler(InlineQueryHandler(inline_query.handler))
    # Group 1: runs only when the group-0 conversation did not handle the
    # update — i.e. the user has no active conversation (never sent /start).
    # The universal dispatcher (plan 1.1) then lets a bare pasted link work
    # even before /start. The format-callback handler is registered BEFORE
    # the stray catch-all: within one group the first matching handler
    # consumes the update, and the catch-all would otherwise swallow it.
    application.add_handler(
        MessageHandler(dispatch.MediaUrlFilter(), dispatch.handle_link), group=1
    )
    application.add_handler(
        CallbackQueryHandler(dispatch.on_yt_format, pattern=YT_FMT_CALLBACK_PATTERN), group=1
    )
    # Song-card buttons (plan 1.2): a user who searched from the menu can
    # click a card after leaving the conversation — the catch-all below
    # must not swallow them.
    application.add_handler(
        CallbackQueryHandler(spotify_search.on_song_action, pattern=SONG_ACTION_CALLBACK_PATTERN), group=1
    )
    application.add_handler(CallbackQueryHandler(handle_stray_callback), group=1)
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
                   # bootstraps instead of dying on the first attempt
                   .connect_timeout(30)
                   .read_timeout(50)
                   .write_timeout(50)
                   .get_updates_read_timeout(50)
                #    .persistence(persistence)
                   .build())

    
    application.bot_data["spotdl_instance"] = spotdl_instance

    register_handlers(application)

    # bootstrap_retries: -1 = keep retrying while the (flaky) proxy is down,
    # instead of dying on the first failed getMe
    application.run_polling(bootstrap_retries=-1)
