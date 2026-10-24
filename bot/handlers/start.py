from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application
from telegram.constants import ChatAction
from utils.constants.messages import HELLO
from utils.constants.states import START_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.helpers import send_html_msg, stringify_none_str, escape_html
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process a /start command."""
    assert update.message is not None
    assert update.effective_user is not None
    user_id = update.effective_user.id
    first_name = escape_html(stringify_none_str(update.effective_user.first_name))
    last_name = escape_html(stringify_none_str(update.effective_user.last_name))
    username = escape_html(stringify_none_str(update.effective_user.username))

    name = " ".join(part for part in (first_name, last_name) if part)
    if username:
        name = f"{name} (@{username})" if name else f"@{username}"

    assert update.effective_chat is not None
    await send_html_msg(
        context.bot,
        update.effective_chat.id,
        HELLO.format(name=name or "there", user_id=user_id),
        keyboards.start_keyboard_rm,
    )

    return START_STATE
