from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from bot.handlers import start
from bot.handlers.instagram import download
from bot.handlers.music.spotify import download as spotify_download
from bot.handlers.pinterest import download as pinterest_download
from bot.handlers.youtube import download as youtube_download, mp3 as youtube_mp3
from bot.handlers.ai import chat, tti
from bot.navigation import navigate
from utils import shared_handlers
from utils.constants.states import (
    START_STATE,
    INSTAGRAM_STATE,
    INSTAGRAM_DOWNLOAD_STATE,
    MUSIC_STATE,
    MUSIC_SPOTIFY_STATE,
    PINTEREST_STATE,
    YOUTUBE_STATE,
    YOUTUBE_DOWNLOAD_STATE,
    YOUTUBE_MP3_STATE,
    AI_STATE,
    AI_CHAT_STATE,
    AI_TTI_STATE,
)


def main_conversation_handler():
    """The main menu conversation.

    Phase A: navigation is inline-keyboard driven — one shared
    CallbackQueryHandler (bot.navigation.navigate) is registered first in
    every state; it dispatches on callback data, so no state needs its own
    button handlers. States that wait for typed input (a URL, a chat
    message, an image prompt) keep a filters.TEXT handler.
    """
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        states={
            START_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
            ],
            INSTAGRAM_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
            ],
            INSTAGRAM_DOWNLOAD_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, download.handler),
            ],
            MUSIC_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
            ],
            MUSIC_SPOTIFY_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, spotify_download.handler),
            ],
            PINTEREST_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, pinterest_download.handler),
            ],
            YOUTUBE_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
            ],
            YOUTUBE_DOWNLOAD_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, youtube_download.handler),
            ],
            YOUTUBE_MP3_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, youtube_mp3.handler),
            ],
            AI_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
            ],
            AI_CHAT_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, chat.handler),
            ],
            AI_TTI_STATE: [
                CallbackQueryHandler(navigate),
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, tti.handler),
            ],
        },
        fallbacks=[],
        name="main_handler",
        # persistent=True,
    )
    return conversation_handler
