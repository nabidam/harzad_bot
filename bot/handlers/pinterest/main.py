"""Pinterest input screen (UX redesign §2.2).

The Pinterest button goes straight here — one hop less than the old
tree. A pasted Pinterest link never reaches this TEXT handler (the
universal link dispatcher runs first in every state); plain text gets
the exact input guidance, never a fake downloader error.
"""
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from utils import copy
from utils.constants.states import PINTEREST_INPUT_STATE
from bot.handlers import common

from utils.decorators import send_action, sync_user, log_message

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """TEXT entry for the Pinterest input screen."""
    if await common.precheck(update, context):
        return PINTEREST_INPUT_STATE
    await common.reprompt(update, context, copy.PINTEREST_PROMPT)
    return PINTEREST_INPUT_STATE
