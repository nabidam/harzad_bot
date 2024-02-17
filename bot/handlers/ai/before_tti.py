from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants.messages import AI_TTI_INSTRUCTION, COMMING_SOON
from utils.constants.states import AI_STATE, AI_TTI_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.helpers import send_md_msg
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_chat is not None
    # # TODO: comming soon
    # await send_md_msg(context.bot, update.effective_chat.id, COMMING_SOON, keyboards.ai_state_keyboard_rm)
    # return AI_STATE
    
    await send_md_msg(context.bot, update.effective_chat.id, AI_TTI_INSTRUCTION, keyboards.ai_tti_state_keyboard_rm)
    return AI_TTI_STATE
