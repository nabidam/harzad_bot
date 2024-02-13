from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters
from bot.handlers import start, instagram
from bot.handlers.instagram import main as instagram, before_download, download
from bot.handlers.music import main as music
from bot.handlers.youtube import main as youtube, before_download as youtube_before_download, before_mp3 as youtube_before_mp3, download as youtube_download, mp3 as youtube_mp3
from bot.handlers.pinterest import main as pinterest, download as pinterest_download
from bot.handlers.music.spotify import main as spotify, download as spotify_download
from utils import shared_handlers
from utils.constants.keyboards import *

from utils.constants.states import *

def main_conversation_handler():
    """Process a /start command."""
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        states={
            START_STATE: [
                MessageHandler(filters.Regex(f"^{INSTAGRAM_KEYBOARD}$"), instagram.handler),
                MessageHandler(filters.Regex(f"^{MUSIC_KEYBOARD}$"), music.handler),
                MessageHandler(filters.Regex(f"^{PINTEREST_KEYBOARD}$"), pinterest.handler),
                MessageHandler(filters.Regex(f"^{YOUTUBE_KEYBOARD}$"), youtube.handler),
                MessageHandler(filters.Regex(f"^{AI_KEYBOARD}$"), youtube.handler),
                *shared_handlers.shared_handlers
            ],

            INSTAGRAM_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{INSTAGRAM_DOWNLOAD_KEYBOARD}$"), before_download.handler),
                *shared_handlers.shared_handlers
            ],
            INSTAGRAM_DOWNLOAD_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), instagram.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, download.handler),
                *shared_handlers.shared_handlers
            ],

            MUSIC_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{MUSIC_SPOTIFY_KEYBOARD}$"), spotify.handler),
                *shared_handlers.shared_handlers
            ],
            MUSIC_SPOTIFY_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), music.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, spotify_download.handler),
                *shared_handlers.shared_handlers
            ],

            PINTEREST_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, pinterest_download.handler),
                *shared_handlers.shared_handlers
            ],

            YOUTUBE_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{YOUTUBE_DOWNLOAD_KEYBOARD}$"), youtube_before_download.handler),
                MessageHandler(filters.Regex(f"^{YOUTUBE_MP3_KEYBOARD}$"), youtube_before_mp3.handler),
                *shared_handlers.shared_handlers
            ],
            YOUTUBE_DOWNLOAD_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), youtube.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, youtube_download.handler),
                *shared_handlers.shared_handlers
            ],
            YOUTUBE_MP3_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), youtube.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, youtube_mp3.handler),
                *shared_handlers.shared_handlers
            ],
            AI_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                *shared_handlers.shared_handlers
            ],
        },
        fallbacks=[],
        name="main_handler",
        persistent=True,
    )
    return conversation_handler