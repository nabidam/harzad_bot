# coding: utf-8
import os
import signal
import sys
from importlib import import_module
from datetime import datetime
from logging import getLogger

from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, Application, PicklePersistence
from bot.conversations.main import main_conversation_handler
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID, DOWNLOAD_MP3_PATH, YT_COOKIE_FILE, YT_PROXY
from utils.constants.messages import SOMETHING_WENT_WRONG

import configurations.settings as settings
from connectors.spotdl import create_spotdl_instance
from utils.custom_context import MyContext
from utils.helpers import create_requirement_folders, react_message
from utils.logger import init_logger
from spotdl import SpotifyClient, Spotdl

logger = getLogger(__name__)


async def post_init(application: Application):
    """Configure the bot's public profile once the application is up.

    Runs inside the network retry loop, so a transient network error here
    is retried before polling starts.
    """
    bot = application.bot
    await bot.set_my_commands([
        BotCommand(command="start", description="Open the menu"),
        BotCommand(command="help", description="How to use the bot"),
    ])
    await bot.set_my_description(
        "Harzad downloads media from Instagram, Spotify, Pinterest and YouTube, "
        "and can chat or generate images with AI. Send /start to begin."
    )
    await bot.set_my_short_description("Media downloader + AI 🎧🎞🤖")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Catch unhandled handler errors.

    Without this, any exception (e.g. a DB outage) silently drops the update
    and the user never hears back. Log the full traceback, then tell the user.
    """
    logger.error("Unhandled exception while handling update", exc_info=context.error)
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

    # spotdl = create_spotdl_instance()

    # persistence = PicklePersistence(filepath="conversation states")
    
    context_types = ContextTypes(context=MyContext)

    application = (ApplicationBuilder()
                   .token(settings.TOKEN)
                #    .context_types(context_types)
                   .post_init(post_init)
                   .read_timeout(50)
                   .write_timeout(50)
                   .get_updates_read_timeout(50)
                #    .persistence(persistence)
                   .build())

    
    application.bot_data["spotdl_instance"] = spotdl_instance

    application.add_handler(main_conversation_handler())
    application.add_error_handler(on_error)

    application.run_polling()
