"""Phase A: central inline navigation.

Every navigation action is a callback id (see utils/constants/keyboards.py)
that names its destination screen. The single `navigate` callback handler
edits the clicked message in place — no more regex matching of typed
button text, no more new message per menu hop.
"""
from logging import getLogger

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils import keyboards
from utils.constants.keyboards import (
    CB_START,
    CB_INSTAGRAM,
    CB_INSTAGRAM_DOWNLOAD,
    CB_MUSIC,
    CB_MUSIC_SPOTIFY,
    CB_PINTEREST,
    CB_YOUTUBE,
    CB_YOUTUBE_DOWNLOAD,
    CB_YOUTUBE_MP3,
    CB_AI,
    CB_AI_CHAT,
)
from utils.constants.messages import (
    AI_CHAT_INSTRUCTION,
    CHOOSE_MESSAGE,
    HELLO,
    INSTAGRAM_INSTRUCTION,
    PINTEREST_INSTRUCTION,
    SPOTIFY_INSTRUCTION,
    YOUTUBE_INSTRUCTION,
)
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
from utils.helpers import escape_html, send_html_msg, stringify_none_str

# Init logger
logger = getLogger(__name__)

# callback data -> (message text, keyboard, conversation state)
NAVIGATOR = {
    CB_START: (CHOOSE_MESSAGE, keyboards.start_keyboard_rm, START_STATE),
    CB_INSTAGRAM: (CHOOSE_MESSAGE, keyboards.instagram_state_keyboard_rm, INSTAGRAM_STATE),
    CB_INSTAGRAM_DOWNLOAD: (INSTAGRAM_INSTRUCTION, keyboards.instagram_download_state_keyboard_rm, INSTAGRAM_DOWNLOAD_STATE),
    CB_MUSIC: (CHOOSE_MESSAGE, keyboards.music_state_keyboard_rm, MUSIC_STATE),
    CB_MUSIC_SPOTIFY: (SPOTIFY_INSTRUCTION, keyboards.music_spotify_state_keyboard_rm, MUSIC_SPOTIFY_STATE),
    CB_PINTEREST: (PINTEREST_INSTRUCTION, keyboards.pinterest_state_keyboard_rm, PINTEREST_STATE),
    CB_YOUTUBE: (CHOOSE_MESSAGE, keyboards.youtube_state_keyboard_rm, YOUTUBE_STATE),
    CB_YOUTUBE_DOWNLOAD: (YOUTUBE_INSTRUCTION, keyboards.youtube_download_state_keyboard_rm, YOUTUBE_DOWNLOAD_STATE),
    CB_YOUTUBE_MP3: (YOUTUBE_INSTRUCTION, keyboards.youtube_mp3_state_keyboard_rm, YOUTUBE_MP3_STATE),
    CB_AI: (CHOOSE_MESSAGE, keyboards.ai_state_keyboard_rm, AI_STATE),
    CB_AI_CHAT: (AI_CHAT_INSTRUCTION, keyboards.ai_chat_state_keyboard_rm, AI_CHAT_STATE),
}


def _hello_text(update: Update) -> str:
    """The start menu shows the full greeting (same as /start)."""
    user = update.effective_user
    assert user is not None
    first_name = escape_html(stringify_none_str(user.first_name))
    last_name = escape_html(stringify_none_str(user.last_name))
    username = escape_html(stringify_none_str(user.username))

    name = " ".join(part for part in (first_name, last_name) if part)
    if username:
        name = f"{name} (@{username})" if name else f"@{username}"

    return HELLO.format(name=name or "there", user_id=user.id)


async def navigate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CallbackQueryHandler for every menu button in the conversation."""
    cq = update.callback_query
    if cq is None:
        return None

    # Acknowledge the click so the client spinner stops immediately.
    await cq.answer()

    item = NAVIGATOR.get(cq.data or "")
    if item is None:
        return None

    text, keyboard, state = item
    if cq.data == CB_START:
        text = _hello_text(update)

    if cq.message is None:
        return state

    try:
        await cq.message.edit_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except BadRequest as err:
        if "not modified" in str(err).lower():
            # Same screen clicked again — nothing to do.
            return state
        # Some messages can't be re-texted (e.g. media messages that carry
        # a Back/Home button): fall back to a fresh menu message.
        logger.warning(f"Could not edit message {cq.message.message_id} in place: {err}")
        assert update.effective_chat is not None
        await send_html_msg(context.bot, update.effective_chat.id, text, keyboard)

    return state
