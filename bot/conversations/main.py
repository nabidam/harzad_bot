"""The main conversation (UX redesign §2).

Six input modes (the old five-button tree is gone — each platform button
goes straight to its input screen):

    HOME / SEARCH_INPUT / INSTAGRAM_INPUT / YOUTUBE_INPUT /
    PINTEREST_INPUT / AI_CHAT

In every mode the same shared chain runs first (bot/handlers/callbacks.py):
tokenized action callbacks, then the stateless navigator, so a pasted
supported link, a card button or a nav tap is handled identically from
any state — and before the mode's own TEXT handler. Plain text is the
mode's business: a query in Search Music, a prompt in AI Chat,
@username for the Instagram profile picture, guidance elsewhere.

/start, /help, /tasks and /cancel are accepted in every mode
(spec §2.3). Nothing in navigation cancels a task.
"""
from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from bot.handlers import callbacks, dispatch, start
from bot.handlers.instagram import download as instagram_download
from bot.handlers.music.spotify import download as spotify_download
from bot.handlers.pinterest import main as pinterest_input
from bot.handlers.youtube import main as youtube_input
from bot.handlers.ai import chat

from utils import shared_handlers
from utils.constants.states import (
    AI_CHAT_STATE,
    HOME_STATE,
    INSTAGRAM_INPUT_STATE,
    PINTEREST_INPUT_STATE,
    SEARCH_INPUT_STATE,
    YOUTUBE_INPUT_STATE,
)

# Plan 1.1: a pasted media link is routed by the universal dispatcher in
# every state, before that state's own TEXT handler (so a YouTube link
# pasted in the AI chat goes to the downloader, not the LLM).
_media_url_filter = dispatch.MediaUrlFilter()
_link_handler = MessageHandler(_media_url_filter, dispatch.handle_link)

_shared_callbacks = callbacks.shared_callback_handlers()

_STATE_HANDLERS = {
    HOME_STATE: MessageHandler(filters.TEXT, start.on_home_text),
    SEARCH_INPUT_STATE: MessageHandler(filters.TEXT, spotify_download.handler),
    INSTAGRAM_INPUT_STATE: MessageHandler(filters.TEXT, instagram_download.handler),
    YOUTUBE_INPUT_STATE: MessageHandler(filters.TEXT, youtube_input.handler),
    PINTEREST_INPUT_STATE: MessageHandler(filters.TEXT, pinterest_input.handler),
    AI_CHAT_STATE: MessageHandler(filters.TEXT, chat.handler),
}


def main_conversation_handler():
    """Build the conversation with one shared handler chain per mode."""
    states = {
        state: [
            *_shared_callbacks,
            _link_handler,
            *shared_handlers.shared_handlers,
            _STATE_HANDLERS[state],
        ]
        for state in _STATE_HANDLERS
    }
    return ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        states=states,
        fallbacks=[],
        name="main_handler",
        # persistent=True,
    )
