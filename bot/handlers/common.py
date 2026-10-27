"""Shared helpers for input-screen TEXT handlers (UX redesign §2.3, §3.1).

Rules shared by every input mode:
- typed "Back 🔙" (legacy button on old screens) -> Home, never a
  platform jump (fixes the old YOUTUBE_DOWNLOAD -> Instagram bug for good)
- a URL that isn't a supported media link -> the supported-platforms
  error with a Home exit — never a fake downloader error, never the LLM
- plain non-link text -> the screen's exact input guidance (re-prompt)
"""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from utils import copy, keyboards, link_detect
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.states import HOME_STATE
from utils.helpers import escape_html

HOME_KEYBOARD = keyboards.home_keyboard


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a fresh Home (used when the current message can't be edited,
    e.g. the user's own typed message)."""
    from bot.navigation import home_text

    assert update.effective_chat is not None
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=home_text(update),
        parse_mode=ParseMode.HTML,
        reply_markup=HOME_KEYBOARD,
    )


async def precheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Run the shared pre-checks on an input screen's plain text.

    Returns True when the message was consumed (legacy Back, unsupported
    URL) and the caller should stop; False means "continue with the
    mode-specific logic".
    """
    assert update.effective_message is not None
    text = update.effective_message.text or ""

    if text == BACK_KEYBOARD:
        await go_home(update, context)
        return True

    url = link_detect.first_url(text)
    if url is not None and link_detect.detect_media(url) is None:
        domain = escape_html(link_detect.url_domain(url))
        assert update.effective_chat is not None
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=copy.ERROR_UNSUPPORTED_DOMAIN.format(domain=domain),
            parse_mode=ParseMode.HTML,
            reply_markup=HOME_KEYBOARD,
        )
        return True

    return False


async def reprompt(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Re-send the screen's exact input guidance (spec §2.3): plain
    non-link text on a link-required screen never triggers a fake
    downloader error."""
    assert update.effective_message is not None
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboards.home_tasks_row_keyboard(),
    )
