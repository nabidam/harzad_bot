from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.states import INSTAGRAM_DOWNLOAD_STATE

from utils.decorators import send_action
from utils.helpers import send_md_msg
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    # if message == BACK_KEYBOARD:
    #     await update.message.reply_text(WHAT_DO_YOU_WANT,
    #                                     reply_markup=base_keyboard)
    #     return HOME_STATE

    assert update.effective_chat is not None
    # message = "This is the image"
    await send_md_msg(context.bot, update.effective_chat.id, message, keyboards.instagram_state_keyboard_rm)
    return INSTAGRAM_DOWNLOAD_STATE
