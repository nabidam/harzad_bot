# coding: utf-8
import os
import signal
import sys
from importlib import import_module
from datetime import datetime

from telegram.ext import ApplicationBuilder, ContextTypes, Application, PicklePersistence
from bot.conversations.main import main_conversation_handler
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID, DOWNLOAD_MP3_PATH, YT_COOKIE_FILE

import configurations.settings as settings
from connectors.spotdl import create_spotdl_instance
from utils.custom_context import MyContext
from utils.helpers import create_requirement_folders
import utils.logger as logger
from spotdl import SpotifyClient, Spotdl

if __name__ == "__main__":
    now = datetime.now()
    strtime = now.strftime("%Y_%m_%d_%H_%M_%S")
    logger.init_logger(f"logs/{settings.NAME}_{strtime}.log")

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
        "yt_dlp_args": f"--cookies {YT_COOKIE_FILE} --ignore-errors --impersonate chrome --force-ipv4 --proxy socks5://127.0.0.1:40000",
        # "yt_dlp_args": f"--proxy socks5://127.0.0.1:40000",
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
                   .read_timeout(50)
                   .write_timeout(50)
                   .get_updates_read_timeout(50)
                #    .persistence(persistence)
                   .build())

    
    application.bot_data["spotdl_instance"] = spotdl_instance

    application.add_handler(main_conversation_handler())

    application.run_polling()
