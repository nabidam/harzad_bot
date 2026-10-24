from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters
from bot.handlers import start, instagram
from bot.handlers.instagram import main as instagram, before_download, download
from bot.handlers.music import main as music
from bot.handlers.youtube import main as youtube, before_download as youtube_before_download, before_mp3 as youtube_before_mp3, download as youtube_download, mp3 as youtube_mp3
from bot.handlers.ai import main as ai, before_chat, chat, before_tti, tti
from bot.handlers.pinterest import main as pinterest, download as pinterest_download
from bot.handlers.music.spotify import main as spotify, download as spotify_download
from utils import shared_handlers
from utils.constants.keyboards import *

from utils.constants.states import *

def main_conversation_handler():
    """Process a /start command."""
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        # In every state, shared_handlers (/start, /help) must come first:
        # /start is text, so a filters.TEXT handler registered before it
        # would shadow the command and eat the message.
        states={
            START_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{INSTAGRAM_KEYBOARD}$"), instagram.handler),
                MessageHandler(filters.Regex(f"^{MUSIC_KEYBOARD}$"), music.handler),
                MessageHandler(filters.Regex(f"^{PINTEREST_KEYBOARD}$"), pinterest.handler),
                MessageHandler(filters.Regex(f"^{YOUTUBE_KEYBOARD}$"), youtube.handler),
                MessageHandler(filters.Regex(f"^{AI_KEYBOARD}$"), ai.handler),
            ],

            INSTAGRAM_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{INSTAGRAM_DOWNLOAD_KEYBOARD}$"), before_download.handler),
            ],
            INSTAGRAM_DOWNLOAD_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), instagram.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, download.handler),
            ],

            MUSIC_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{MUSIC_SPOTIFY_KEYBOARD}$"), spotify.handler),
            ],
            MUSIC_SPOTIFY_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), music.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, spotify_download.handler),
            ],

            PINTEREST_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, pinterest_download.handler),
            ],

            YOUTUBE_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{YOUTUBE_DOWNLOAD_KEYBOARD}$"), youtube_before_download.handler),
                MessageHandler(filters.Regex(f"^{YOUTUBE_MP3_KEYBOARD}$"), youtube_before_mp3.handler),
            ],
            YOUTUBE_DOWNLOAD_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), youtube.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, youtube_download.handler),
            ],
            YOUTUBE_MP3_STATE: [
                *shared_handlers.shared_handlers,
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), youtube.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, youtube_mp3.handler),
            ],
            
            AI_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.Regex(f"^{AI_CHAT_KEYBOARD}$"), before_chat.handler),
                MessageHandler(filters.Regex(f"^{AI_TTI_KEYBOARD}$"), before_tti.handler),
            ],
            AI_CHAT_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), ai.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, chat.handler),
            ],
            AI_TTI_STATE: [
                *shared_handlers.shared_handlers,

                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), ai.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                MessageHandler(filters.TEXT, tti.handler),
            ],
        },
        fallbacks=[],
        name="main_handler",
        # persistent=True,
    )
    return conversation_handler