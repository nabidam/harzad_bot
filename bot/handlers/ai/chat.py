import os
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
import validators
import re
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode
from configurations.settings import DOWNLOAD_VIDEO_PATH, HOST_ROOT, HOSTNAME, OPENAI_API_KEY
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import BOT_ID, DIRECT_VIDEO_LINK, DOWNLOADING_VIDEO, GETTING_MEDIA_INFORMATION, GETTING_PROFILE_INFORMATION, GETTING_STORY_INFORMATION, INVALID_PINTEREST_URL, LINK_IS_INVALID, MEDIA_CAPTION, MEDIA_NOT_FOUND, PROCESSING, SENDING_THUMBNAIL, SENDING_VIDEO, SOMETHING_WENT_WRONG, USER_NOT_FOUND_CHECK_USERNAME_AND_TRY_AGAIN
from utils.constants.states import AI_CHAT_STATE

from utils.decorators import send_action
from utils.exceptions.instagram import LoginException
from utils.helpers import escape_md, prepare_link, send_md_msg
from utils import keyboards

from utils.instagram import get_user_client_instagram

from yt_dlp import YoutubeDL, utils
import moviepy.editor as mp

# Init logger
logger = getLogger(__name__)

template = """You are a friendly chatbot, response to user's message with a friendly tone.
User message: {message}"""
prompt = PromptTemplate(template=template, input_variables=["message"])
llm = ChatOpenAI(model='gpt-3.5-turbo')
chain = LLMChain(prompt=prompt, llm=llm)

@send_action(ChatAction.TYPING)
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    message = update.message.text
    assert message is not None
    print(message)

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    
    llm_response = chain.invoke({"message": message})
    
    print(llm_response)
    
    await send_md_msg(context.bot, update.effective_chat.id, llm_response["text"], keyboards.ai_chat_state_keyboard_rm)
    
    return AI_CHAT_STATE