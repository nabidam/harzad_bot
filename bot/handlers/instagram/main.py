from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants.messages import CHOOSE_MESSAGE
from utils.constants.states import INSTAGRAM_STATE

from utils.decorators import send_action
from utils.helpers import send_md_msg
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_chat is not None
    await send_md_msg(context.bot, update.effective_chat.id, CHOOSE_MESSAGE, keyboards.instagram_state_keyboard_rm)
    return INSTAGRAM_STATE
