from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application
from telegram.constants import ChatAction
from utils.constants.messages import HELP
from utils.constants.states import START_STATE

from utils.decorators import send_action, sync_user
from utils.helpers import send_md_msg, stringify_none_str
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process a /help command."""
    assert update.effective_chat is not None
    
    await send_md_msg(context.bot, update.effective_chat.id, HELP, keyboards.start_keyboard_rm)
