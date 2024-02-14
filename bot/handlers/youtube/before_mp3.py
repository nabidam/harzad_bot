from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants.messages import YOUTUBE_INSTRUCTION
from utils.constants.states import YOUTUBE_MP3_STATE

from utils.decorators import send_action, sync_user
from utils.helpers import send_md_msg
from utils import keyboards

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_chat is not None
    await send_md_msg(context.bot, update.effective_chat.id, YOUTUBE_INSTRUCTION, keyboards.youtube_download_state_keyboard_rm)
    return YOUTUBE_MP3_STATE
