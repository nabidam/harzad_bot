import os
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
import validators
import re
from logging import getLogger
import together
import base64

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from configurations.settings import TOGETHER_API_KEY
from utils.constants import *
from utils.constants.messages import AI_PROMPT_FOR_CAPTION, PROCESSING
from utils.constants.states import AI_TTI_STATE

from utils.decorators import send_action, sync_user
from utils.helpers import escape_md, prepare_link, send_image, send_md_msg
from utils import keyboards

# Init logger
logger = getLogger(__name__)

together.api_key = TOGETHER_API_KEY

model = "stabilityai/stable-diffusion-xl-base-1.0"
# model = "prompthero/openjourney"
# model = "stabilityai/stable-diffusion-2-1"
# model = "SG161222/Realistic_Vision_V3.0_VAE"
# model = "wavymulder/Analog-Diffusion"

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
    
    together_response = together.Image.create(
        prompt = message, 
        model = model, 
        width= 512,
        height= 512,
        steps= 40,
        seed= 6616
    )
    
    print(together_response)
    
    image = together_response["output"]["choices"][0]
    image_content = base64.b64decode(image["image_base64"])
    
    await context.bot.deleteMessage(
        message_id=bot_message.message_id,
        chat_id=update.message.chat_id,
    )
    
    caption = AI_PROMPT_FOR_CAPTION.format(prompt=message)
    await send_image(context.bot, update.effective_chat.id, image_content, caption, keyboards.ai_chat_state_keyboard_rm)
    
    return AI_TTI_STATE