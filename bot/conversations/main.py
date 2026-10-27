from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from bot.handlers import dispatch, start
from bot.handlers.instagram import download
from bot.handlers.music.spotify import download as spotify_download
from bot.handlers.pinterest import download as pinterest_download
from bot.handlers.youtube import download as youtube_download, mp3 as youtube_mp3
from bot.handlers.ai import chat
from bot.navigation import navigate
from utils import shared_handlers
from utils.constants.keyboards import YT_FMT_CALLBACK_PATTERN
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
)

# Plan 1.1: a pasted media link is routed by the universal dispatcher in
# every state, before that state's own TEXT handler (so a YouTube link
# pasted in the AI chat goes to the downloader, not the LLM).
_media_url_filter = dispatch.MediaUrlFilter()
_yt_format_cb = CallbackQueryHandler(dispatch.on_yt_format, pattern=YT_FMT_CALLBACK_PATTERN)
_link_handler = MessageHandler(_media_url_filter, dispatch.handle_link)


def main_conversation_handler():
    """The main menu conversation.

    Phase A: navigation is inline-keyboard driven — the shared
    CallbackQueryHandler (bot.navigation.navigate) dispatches on callback
    data, so no state needs its own button handlers. States that wait for
    typed input (a URL, a chat message, an image prompt) keep a
    filters.TEXT handler.

    Phase 1.1: the universal link dispatcher runs in every state — its
    URL filter is placed before the state's own TEXT handler so a pasted
    link never leaks into state input parsing. The YouTube format
    selector (pattern-matched) precedes navigate, because
    check_update picks the first matching handler per update.
    """
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        states={
            START_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
            ],
            INSTAGRAM_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
            ],
            INSTAGRAM_DOWNLOAD_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, download.handler),
            ],
            MUSIC_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
            ],
            MUSIC_SPOTIFY_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, spotify_download.handler),
            ],
            PINTEREST_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, pinterest_download.handler),
            ],
            YOUTUBE_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
            ],
            YOUTUBE_DOWNLOAD_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, youtube_download.handler),
            ],
            YOUTUBE_MP3_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, youtube_mp3.handler),
            ],
            AI_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
            ],
            AI_CHAT_STATE: [
                _yt_format_cb,
                CallbackQueryHandler(navigate),
                _link_handler,
                *shared_handlers.shared_handlers,
                MessageHandler(filters.TEXT, chat.handler),
            ],
        },
        fallbacks=[],
        name="main_handler",
        # persistent=True,
    )
    return conversation_handler
