from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application
from telegram.constants import ChatAction
from utils.constants.messages import HELLO, STATE_INDICATOR
from utils.constants.states import START_STATE

from utils.decorators import send_action
from utils.helpers import send_md_msg, stringify_none_str
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process a /start command."""
    assert update.message is not None
    message = update.message.text
    assert update.effective_user is not None
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    last_name = update.effective_user.last_name
    username = update.effective_user.username

    assert update.effective_chat is not None
    message = HELLO.format(message=stringify_none_str(message), user_id=user_id, first_name=stringify_none_str(first_name), last_name=stringify_none_str(last_name), username=stringify_none_str(username))
    await send_md_msg(context.bot, update.effective_chat.id, message, keyboards.start_keyboard_rm)

    message = STATE_INDICATOR.format(state="start")
    await send_md_msg(context.bot, update.effective_chat.id, message, keyboards.start_keyboard_rm)
    return START_STATE
