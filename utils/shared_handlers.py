from telegram.ext import CommandHandler

from bot.handlers import start, help

shared_handlers = [
    CommandHandler("start", start.handler),
    CommandHandler("help", help.handler),
]