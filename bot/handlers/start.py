from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils import copy, keyboards
from utils.constants.states import HOME_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.helpers import send_html_msg
from bot.navigation import home_text

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start: always a fresh Home (spec §2.3). Never cancels tasks."""
    assert update.message is not None
    assert update.effective_user is not None
    assert update.effective_chat is not None

    await send_html_msg(
        context.bot,
        update.effective_chat.id,
        home_text(update),
        keyboards.home_keyboard,
    )

    return HOME_STATE


async def on_home_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Plain non-link text on Home (spec §2.3): no input mode is active,
    so point at the two real actions instead of guessing."""
    assert update.effective_message is not None
    await update.effective_message.reply_text(
        copy.HOME_TEXT_HINT,
        parse_mode="HTML",
        reply_markup=keyboards.home_keyboard,
    )
    return HOME_STATE
