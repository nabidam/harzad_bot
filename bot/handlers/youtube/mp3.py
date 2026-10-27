import validators
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode
from configurations.settings import DOWNLOAD_MP3_PATH, YT_COOKIE_FILE
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import BOT_ID, DOWNLOADING_VIDEO, GETTING_MEDIA_INFORMATION, LINK_IS_INVALID, PROCESSING, SOMETHING_WENT_WRONG
from utils.constants.states import INSTAGRAM_STATE, YOUTUBE_MP3_STATE

from utils.decorators import send_action, sync_user, log_message
from utils import keyboards
from utils.helpers import react_message
from utils.storage import cleanup_media

from yt_dlp import YoutubeDL, utils
from pydub import AudioSegment

# Init logger
logger = getLogger(__name__)

ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': DOWNLOAD_MP3_PATH + '%(title)s.%(ext)s',
    'restrictfilenames': True,
    'cookiefile': YT_COOKIE_FILE,
    # 'writethumbnail': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }]
}


async def run_mp3_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Core YouTube/yt-dlp MP3 flow, shared by the menu state handler and
    the universal link dispatcher (plan 1.1)."""
    assert update.effective_chat is not None
    assert update.effective_message is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    bot_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=PROCESSING)

    with YoutubeDL(ydl_opts) as ydl:
        await context.bot.editMessageText(
            message_id=bot_message.message_id,
            chat_id=update.effective_chat.id,
            text=GETTING_MEDIA_INFORMATION,
        )

        try:
            info_dict = ydl.extract_info(url, download=False)
        except utils.DownloadError:
            await react_message(update, context.bot, "👎")
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.effective_chat.id,
                text=SOMETHING_WENT_WRONG,
            )
            return

        is_playlist = info_dict.get("_type") == PLAYLIST

        await context.bot.editMessageText(
            message_id=bot_message.message_id,
            chat_id=update.effective_chat.id,
            text=DOWNLOADING_VIDEO,
        )

        try:
            ydl.download([url])

            if is_playlist:
                items = info_dict.get('entries')
                if not items:
                    await react_message(update, context.bot, "👎")
                    await context.bot.editMessageText(
                        message_id=bot_message.message_id,
                        chat_id=update.effective_chat.id,
                        text=SOMETHING_WENT_WRONG,
                    )
                    return
                await context.bot.deleteMessage(
                    message_id=bot_message.message_id,
                    chat_id=update.effective_chat.id,
                )
                for index, item in enumerate(items):
                    ydl.process_info(item)
                    title = item.get('title')
                    ext = item.get('ext')
                    filename = ydl.prepare_filename(item)
                    thumbnail_url = item.get("thumbnail")
                    with open(filename, "rb") as f:
                        # check for size

                        # Load the MP3 file
                        audio = AudioSegment.from_file(filename, format="mp3")

                        duration = int(len(audio)/1000)

                        await context.bot.send_chat_action(
                            chat_id=update.effective_message.chat_id,
                            action=ChatAction.UPLOAD_VOICE,
                        )
                        await context.bot.send_audio(
                            chat_id=update.effective_chat.id, 
                            audio=f, 
                            duration=duration, 
                            thumbnail=thumbnail_url, 
                            caption=BOT_ID,  
                            protect_content=True,
                            reply_markup=keyboards.youtube_mp3_state_keyboard_rm
                        )
                        await react_message(update, context.bot, "👍")
                        # delivered to Telegram: the local file is staging only
                        cleanup_media(filename)
            else:
                ydl.process_info(info_dict)
                title = info_dict.get('title')
                ext = info_dict.get('ext')
                filename = ydl.prepare_filename(info_dict)
                thumbnail_url = info_dict.get("thumbnail")
                with open(filename, "rb") as f:
                    # check for video size

                    # Load the MP3 file
                    audio = AudioSegment.from_file(filename, format="mp3")

                    duration = int(len(audio)/1000)

                    await context.bot.deleteMessage(
                        message_id=bot_message.message_id,
                        chat_id=update.effective_chat.id,
                    )

                    await context.bot.send_chat_action(
                        chat_id=update.effective_message.chat_id,
                        action=ChatAction.UPLOAD_VOICE,
                    )
                    await context.bot.send_audio(
                        chat_id=update.effective_chat.id, 
                        audio=f, 
                        duration=duration, 
                        thumbnail=thumbnail_url, 
                        caption=BOT_ID,  
                        protect_content=True,
                        reply_markup=keyboards.youtube_mp3_state_keyboard_rm
                    )
                    await react_message(update, context.bot, "👍")
                    # delivered to Telegram: the local file is staging only
                    cleanup_media(filename)

        except utils.DownloadError:
            await react_message(update, context.bot, "👎")
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.effective_chat.id,
                text=SOMETHING_WENT_WRONG,
            )


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    message = update.message.text
    assert message is not None
    print(message)
    if message == BACK_KEYBOARD:
        await update.message.reply_text("backed", reply_markup=keyboards.start_keyboard_rm)
        return INSTAGRAM_STATE

    message_is_url = validators.url(message) # type: ignore

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    if message_is_url:
        await run_mp3_download(update, context, message)
    else:
        await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        bot_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=PROCESSING)
        await react_message(update, context.bot, "👎")
        await context.bot.editMessageText(
            message_id=bot_message.message_id,
            chat_id=update.effective_chat.id,
            text=LINK_IS_INVALID,
        )

    return YOUTUBE_MP3_STATE
