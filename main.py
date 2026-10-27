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
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes, Application, PicklePersistence
from PIL import Image
from bot.conversations.main import main_conversation_handler
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID, DOWNLOAD_MP3_PATH, YT_COOKIE_FILE, YT_PROXY, SCRIPT_DIR
from utils.constants.messages import SOMETHING_WENT_WRONG
from web.app import start_server

import configurations.settings as settings
from connectors.spotdl import create_spotdl_instance
from utils.custom_context import MyContext
from utils.helpers import create_requirement_folders, react_message
from utils.logger import init_logger
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


async def answer_stray_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group-1 catch-all for inline-button clicks.

    Inline buttons stay clickable forever after they are sent. If the user
    clicks one while no conversation is active (never sent /start), the
    conversation handler in group 0 does not match the update — without this,
    nobody calls answerCallbackQuery and the client's button spinner hangs.
    """
    cq = update.callback_query
    if cq is not None:
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

    application.add_handler(main_conversation_handler())
    # Group 1: if no conversation is active yet (user never sent /start),
    # the conversation in group 0 ignores the update and this catch-all
    # answers the click so the client's button spinner doesn't hang.
    # When the conversation does handle a callback (block=True), this group
    # is skipped, so there is no double handling.
    application.add_handler(CallbackQueryHandler(answer_stray_callback), group=1)
    application.add_error_handler(on_error)

    # bootstrap_retries: -1 = keep retrying while the (flaky) proxy is down,
    # instead of dying on the first failed getMe
    application.run_polling(bootstrap_retries=-1)
