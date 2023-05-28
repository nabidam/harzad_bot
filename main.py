# coding: utf-8
import os
import signal
import sys
from importlib import import_module

from telegram.ext import ApplicationBuilder, Application, PicklePersistence
from bot.conversations.main import main_conversation_handler

import configurations.settings as settings
from utils.helpers import create_requirement_folders
import utils.logger as logger


if __name__ == "__main__":
    logger.init_logger(f"logs/{settings.NAME}.log")

    create_requirement_folders()

    persistence = PicklePersistence(filepath="conversation states")

    application = (ApplicationBuilder()
                   .token(settings.TOKEN)
                   .read_timeout(50)
                   .write_timeout(50)
                   .get_updates_read_timeout(50)
                   .persistence(persistence)
                   .build())

    application.add_handler(main_conversation_handler())

    application.run_polling()
