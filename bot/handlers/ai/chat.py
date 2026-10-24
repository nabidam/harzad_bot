import os
from langchain_openai import ChatOpenAI
import validators
import re
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode
from configurations.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import BOT_ID, DIRECT_VIDEO_LINK, DOWNLOADING_VIDEO, GETTING_MEDIA_INFORMATION, GETTING_PROFILE_INFORMATION, GETTING_STORY_INFORMATION, INVALID_PINTEREST_URL, LINK_IS_INVALID, MEDIA_CAPTION, MEDIA_NOT_FOUND, PROCESSING, SENDING_THUMBNAIL, SENDING_VIDEO, SOMETHING_WENT_WRONG, USER_NOT_FOUND_CHECK_USERNAME_AND_TRY_AGAIN
from utils.constants.states import AI_CHAT_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.exceptions.instagram import LoginException
from utils.helpers import escape_html, send_html_msg
from utils import keyboards

from utils.instagram import get_user_client_instagram

# Init logger
logger = getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Harzad, a friendly Telegram media-download bot. "
    "Reply to the user's message in a friendly, conversational tone. "
    "Keep answers short (a few sentences) and use plain text only."
)

# Any OpenAI-compatible server (vLLM, Ollama, LM Studio, ...) — see settings.
llm = ChatOpenAI(
    model=LLM_MODEL,
    openai_api_base=LLM_BASE_URL,
    openai_api_key=LLM_API_KEY,
)

@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    message = update.message.text
    assert message is not None

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    
    # ainvoke: inference runs in a worker thread, so a slow local model
    # can't freeze the whole bot (the old chain.invoke blocked the loop).
    response = await llm.ainvoke(
        [("system", SYSTEM_PROMPT), ("user", message)]
    )
    logger.debug("AI chat response: %s", response.content)
    
    await send_html_msg(context.bot, update.effective_chat.id, escape_html(response.content), keyboards.ai_chat_state_keyboard_rm)
    
    return AI_CHAT_STATE