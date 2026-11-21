"""The main conversation (UX redesign §2).

Seven input modes (the old five-button tree is gone — each platform button
goes straight to its input screen):

    HOME / SEARCH_INPUT / INSTAGRAM_INPUT / YOUTUBE_INPUT /
    PINTEREST_INPUT / AI_CHAT / LIBRARY_SEARCH

LIBRARY_SEARCH (music library in-library search, plan §12.6) is the
odd one out: its /cancel means "stop searching" (not a task cancel)
and a pasted media link hands off to the download flow instead of
being treated as a query.

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
from bot.handlers.music import library as music_library
from bot.handlers.music.spotify import download as spotify_download
from bot.handlers.pinterest import main as pinterest_input
from bot.handlers.youtube import main as youtube_input
from bot.handlers.ai import chat

from utils import shared_handlers
from utils.constants.states import (
    AI_CHAT_STATE,
    HOME_STATE,
    INSTAGRAM_INPUT_STATE,
    LIBRARY_SEARCH_STATE,
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


async def _lib_search_link(update, context):
    """A media link pasted during the library search prompt (owner
    decision, plan §12.1.1): hand it to the normal download flow and
    leave the search state — the next plain text means Home again."""
    context.user_data.pop(music_library.LIB_SEARCH_CARD_KEY, None)
    await dispatch.handle_link(update, context)
    return HOME_STATE


_LIB_SEARCH_LINK = MessageHandler(_media_url_filter, _lib_search_link)
# In this state /cancel means "stop searching" — it must sit BEFORE the
# shared task-cancel handler (spec: /cancel is otherwise a task verb).
_LIB_SEARCH_CANCEL = CommandHandler("cancel",
                                    music_library.on_lib_search_cancel_command)

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
    # The library search prompt (plan §12.6): the shared callback chain
    # first (so lib:*/nav:* work mid-prompt), then the search /cancel,
    # then the link handoff, the shared commands, and the state's TEXT
    # handler (the query itself).
    states[LIBRARY_SEARCH_STATE] = [
        *_shared_callbacks,
        _LIB_SEARCH_CANCEL,
        _LIB_SEARCH_LINK,
        *shared_handlers.shared_handlers,
        MessageHandler(filters.TEXT, music_library.on_lib_search_text),
    ]
    return ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        states=states,
        fallbacks=[],
        name="main_handler",
        # persistent=True,
    )
