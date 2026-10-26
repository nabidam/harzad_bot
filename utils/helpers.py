import html
import io
import logging
import os
import random
from telegram import InputFile, ReactionTypeEmoji
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ExtBot

from PIL import Image
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

async def react_message(update, bot: ExtBot, emoji: str):
    """Add an emoji reaction to the update's message (Bot API 6.0).

    Reactions are decorative: Telegram only accepts emoji from its
    reaction set (anything else -> BadRequest: Reaction_invalid), so
    swallow the error instead of breaking the handler flow.
    """
    if update.effective_message is None or update.effective_chat is None:
        return
    try:
        await bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
            reaction=ReactionTypeEmoji(emoji=emoji),
        )
    except TelegramError as err:
        logging.warning(f"Could not set reaction {emoji!r}: {err}")

def escape_md(text):
    # The full Markdown-V2 special set, escaped explicitly: re.escape()
    # only escapes regex-special characters and its set changed across
    # Python versions (3.7 dropped `_`, `!`, `>`, ...), which silently
    # produced unescaped MDv2 specials -> Telegram parse errors.
    mdv2_specials = set(r"\_*[]()~`>#+-=|{}.!")
    return "".join(
        "\\" + ch if ch in mdv2_specials else ch for ch in str(text)
    )

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


def prepare_cover(video_path):
    """Prepare the yt-dlp thumbnail written next to a video for Telegram
    (Bot API 8.3, used as both the inline `thumbnail` and the persistent
    `cover`).

    Covers must be a JPEG under 512x512 px and 200 KB, so the image is
    downscaled and re-encoded in memory. yt-dlp names the thumbnail after
    the video with a .jpg/.jpeg/.webp/.png extension (YouTube currently
    serves .webp). Returns a fresh in-memory InputFile, or None when no
    thumbnail exists — the video then ships without one instead of failing.
    """
    base = os.path.splitext(video_path)[0]
    thumb = next(
        (base + ext for ext in (".jpg", ".jpeg", ".webp", ".png") if os.path.isfile(base + ext)),
        None,
    )
    if thumb is None:
        return None
    try:
        img = Image.open(thumb).convert("RGB")
        img.thumbnail((480, 480), Image.LANCZOS)
        buf = io.BytesIO()
        for quality in (85, 70, 55):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= 190_000:
                break
        return InputFile(buf.getvalue(), filename="cover.jpg")
    except Exception:
        logging.getLogger(__name__).debug("Could not prepare cover from %s", thumb, exc_info=True)
        return None