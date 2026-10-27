"""YouTube input screen (UX redesign §2.2, §3).

The YouTube button goes straight here. A pasted YouTube link never
reaches this TEXT handler: the universal dispatcher shows the format
picker (the only path to a YouTube download — no forced 720p, no
forced MP3, spec §3.1). Plain text gets the exact input guidance.
"""
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from utils import copy
from utils.constants.states import YOUTUBE_INPUT_STATE
from bot.handlers import common

from utils.decorators import send_action, sync_user, log_message

# Init logger
logger = getLogger(__name__)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """TEXT entry for the YouTube input screen."""
    if await common.precheck(update, context):
        return YOUTUBE_INPUT_STATE
    await common.reprompt(update, context, copy.YOUTUBE_PROMPT)
    return YOUTUBE_INPUT_STATE
