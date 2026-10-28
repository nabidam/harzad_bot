from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils import copy, keyboards
from utils.ack import answer_callback

from utils.decorators import send_action, sync_user, log_message
from utils.helpers import send_html_msg

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help: the Help page with its own buttons (spec §13.1).

    Does not change the input mode — help is a peek, not a screen.
    """
    assert update.effective_chat is not None

    await send_html_msg(context.bot, update.effective_chat.id, copy.HELP_BODY, keyboards.help_keyboard)
    return None


async def on_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`nav:help` button: same page as /help (spec §2.3)."""
    cq = update.callback_query
    if cq is None:
        return None
    await answer_callback(cq)
    assert update.effective_chat is not None
    if cq.message is not None:
        try:
            await cq.message.edit_text(
                copy.HELP_BODY,
                parse_mode="HTML",
                reply_markup=keyboards.help_keyboard,
            )
            return None
        except Exception:
            logger.warning("nav:help: could not edit message in place", exc_info=True)
    await send_html_msg(context.bot, update.effective_chat.id, copy.HELP_BODY, keyboards.help_keyboard)
    return None
