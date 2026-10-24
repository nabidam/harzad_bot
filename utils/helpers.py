import html
import os
import random
from telegram.constants import ParseMode
from telegram.ext import ExtBot

import re
from configurations.settings import HOST_ROOT, HOSTNAME, PROXY_LIST

from utils.constants import LOGIN

import urllib.parse

def create_requirement_folders():
    """create requirement folders"""
    current_directory = os.getcwd()

    download_directory = f"{current_directory}/download"
    download_directory_is_exist = os.path.exists(download_directory)
    if not download_directory_is_exist:
        os.makedirs(download_directory)

    login_directory = f"{current_directory}/{LOGIN.lower()}"
    login_directory_is_exist = os.path.exists(login_directory)
    if not login_directory_is_exist:
        os.makedirs(login_directory)

def stringify_none_str(str):
    return str if str is not None else ""

def escape_md(text):
    escape_chars = r"\_*[]()~>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)

def escape_html(text):
    """Escape user-provided text so it is safe inside an HTML parse-mode message."""
    return html.escape(str(text), quote=False)

async def send_msg(bot, receiver, msg):
    await bot.send_message(chat_id=receiver, text=msg)

async def send_md_msg(bot, receiver, msg, keyboard = None):
    text = escape_md(msg)

    await bot.send_message(chat_id=receiver, text=text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)

async def send_html_msg(bot, receiver, msg, keyboard = None):
    """Send a message using HTML parse mode.

    Bot-authored templates may contain HTML tags; anything user-provided
    must be escaped with escape_html() before being formatted in.
    """
    await bot.send_message(chat_id=receiver, text=msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def send_image(bot: ExtBot, receiver, image, caption = None, keyboard = None):
    await bot.send_photo(
        chat_id=receiver,
        photo=image,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        protect_content=True,
    )

def next_proxy():
    print(PROXY_LIST)
    if PROXY_LIST is not None:
        choice = random.choices(PROXY_LIST)
        return choice[0]
    return ""

def prepare_link(path):
    # return urllib.parse.quote(url)
    return (path.replace(HOST_ROOT, HOSTNAME)).replace(" ", "%20")