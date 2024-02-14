import os
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
import validators
import re
from logging import getLogger
import replicate

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants import *
from utils.constants.messages import PROCESSING
from utils.constants.states import AI_TTI_STATE

from utils.decorators import send_action, sync_user
from utils.helpers import escape_md, prepare_link, send_image, send_md_msg
from utils import keyboards

# Init logger
logger = getLogger(__name__)

repo_id = "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b"
# repo_id = "ai-forever/kandinsky-2.2:ea1addaab376f4dc227f5368bbd8eff901820fd1cc14ed8cad63b29249e9d463"

@send_action(ChatAction.TYPING)
@sync_user
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    message = update.message.text
    assert message is not None
    print(message)

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    bot_message = await context.bot.send_message(chat_id=update.message.chat_id, text=PROCESSING)
    
    replicate_response = replicate.run(repo_id,input={"prompt": message})
    
    print(replicate_response)
    
    await context.bot.deleteMessage(
        message_id=bot_message.message_id,
        chat_id=update.message.chat_id,
    )
    
    await send_image(context.bot, update.effective_chat.id, replicate_response[0], keyboards.ai_chat_state_keyboard_rm)
    
    return AI_TTI_STATE