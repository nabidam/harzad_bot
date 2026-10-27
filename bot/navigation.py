"""Central inline navigation (UX redesign §2).

Every navigation action is a stateless callback id naming its destination
screen — the same dispatcher works from any state and before /start, and
navigating NEVER cancels active work (spec §1.3: a task is not a screen).

Legacy menu-tree callbacks from the pre-redesign UI stay mapped here as
aliases (spec §16), so buttons on old messages keep working after a
restart. The old `YOUTUBE_DOWNLOAD -> Instagram` back bug is gone with
the old tree: every back destination is Home.
"""
from logging import getLogger

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils import copy, keyboards
from utils.constants.keyboards import (
    CB_AI,
    CB_AI_CHAT,
    CB_AI_TTI,
    CB_HOME,
    CB_INSTAGRAM,
    CB_INSTAGRAM_DOWNLOAD,
    CB_MUSIC,
    CB_MUSIC_SPOTIFY,
    CB_PINTEREST,
    CB_SEARCH,
    CB_START,
    CB_YOUTUBE,
    CB_YOUTUBE_DOWNLOAD,
    CB_YOUTUBE_MP3,
)
from utils.constants.states import (
    AI_CHAT_STATE,
    HOME_STATE,
    INSTAGRAM_INPUT_STATE,
    PINTEREST_INPUT_STATE,
    SEARCH_INPUT_STATE,
    YOUTUBE_INPUT_STATE,
)
from utils.helpers import escape_html, send_html_msg, stringify_none_str

# Init logger
logger = getLogger(__name__)


def home_text(update: Update) -> str:
    """The Home greeting (spec §2.1): first name only — no user ID,
    no bot ID."""
    user = update.effective_user
    assert user is not None
    first_name = escape_html(stringify_none_str(user.first_name))
    return copy.HOME_GREETING.format(first_name=first_name or "there")


# callback data -> (message text, keyboard, conversation state)
# Texts are templates from the canonical copy deck (utils/copy.py).
NAVIGATOR = {
    CB_START: (home_text, keyboards.home_keyboard, HOME_STATE),
    CB_HOME: (home_text, keyboards.home_keyboard, HOME_STATE),
    CB_SEARCH: (copy.SEARCH_PROMPT, keyboards.search_input_keyboard, SEARCH_INPUT_STATE),
    # legacy music tree -> the one Search Music input screen
    CB_MUSIC: (copy.SEARCH_PROMPT, keyboards.search_input_keyboard, SEARCH_INPUT_STATE),
    CB_MUSIC_SPOTIFY: (copy.SEARCH_PROMPT, keyboards.search_input_keyboard, SEARCH_INPUT_STATE),
    # one hop less: platform button goes straight to its input prompt
    CB_INSTAGRAM: (copy.INSTAGRAM_PROMPT, keyboards.instagram_input_keyboard, INSTAGRAM_INPUT_STATE),
    CB_INSTAGRAM_DOWNLOAD: (copy.INSTAGRAM_PROMPT, keyboards.instagram_input_keyboard, INSTAGRAM_INPUT_STATE),
    CB_PINTEREST: (copy.PINTEREST_PROMPT, keyboards.pinterest_input_keyboard, PINTEREST_INPUT_STATE),
    CB_YOUTUBE: (copy.YOUTUBE_PROMPT, keyboards.youtube_input_keyboard, YOUTUBE_INPUT_STATE),
    CB_YOUTUBE_DOWNLOAD: (copy.YOUTUBE_PROMPT, keyboards.youtube_input_keyboard, YOUTUBE_INPUT_STATE),
    CB_YOUTUBE_MP3: (copy.YOUTUBE_PROMPT, keyboards.youtube_input_keyboard, YOUTUBE_INPUT_STATE),
    CB_AI: (copy.AI_PROMPT, keyboards.ai_chat_keyboard, AI_CHAT_STATE),
    CB_AI_CHAT: (copy.AI_PROMPT, keyboards.ai_chat_keyboard, AI_CHAT_STATE),
}

# Back mapping (spec §2.4). The new IA has exactly one parent for every
# screen: Home. Kept as an explicit table so a deeper tree in a future
# release only changes this dict.
BACK_DESTINATION = {
    "search": HOME_STATE,
    "instagram": HOME_STATE,
    "youtube": HOME_STATE,
    "pinterest": HOME_STATE,
    "ai": HOME_STATE,
    "tasks": HOME_STATE,
    "legacy": HOME_STATE,
}


async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`nav:back:<screen>` -> the screen's parent (always Home today)."""
    cq = update.callback_query
    if cq is None:
        return None
    await cq.answer()
    screen = (cq.data or "").split(":", 2)[2]
    state = BACK_DESTINATION.get(screen, HOME_STATE)
    await _show_screen(update, context, home_text(update), keyboards.home_keyboard)
    return state


async def on_ai_tti_stale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Defensive handler for the retired Text-to-image button (spec §10).

    Old messages may still carry `nav:ai:ttI`; answer with a toast so the
    client spinner stops, and never change state.
    """
    cq = update.callback_query
    if cq is None:
        return None
    await cq.answer(copy.TOAST_IMAGE_GEN_UNAVAILABLE)
    return None


async def _show_screen(update: Update, context, text, keyboard) -> None:
    """Edit the clicked message into `text`+`keyboard`; send fresh if the
    message can't be re-texted (e.g. it carries media)."""
    cq = update.callback_query
    assert cq is not None
    if cq.message is None:
        return
    try:
        await cq.message.edit_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except BadRequest as err:
        if "not modified" in str(err).lower():
            return
        logger.warning(f"Could not edit message {cq.message.message_id} in place: {err}")
        assert update.effective_chat is not None
        await send_html_msg(context.bot, update.effective_chat.id, text, keyboard)


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
    if callable(text):
        # Home's greeting needs the user's first name at render time.
        text = text(update)
    await _show_screen(update, context, text, keyboard)
    return state
