import validators
import re
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode
from configurations.settings import DOWNLOAD_MP3_PATH
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import BOT_ID, DOWNLOADING_VIDEO, GETTING_MEDIA_INFORMATION, GETTING_PROFILE_INFORMATION, GETTING_STORY_INFORMATION, INVALID_PINTEREST_URL, LINK_IS_INVALID, MEDIA_CAPTION, MEDIA_NOT_FOUND, PROCESSING, SENDING_THUMBNAIL, SENDING_VIDEO, SOMETHING_WENT_WRONG, USER_NOT_FOUND_CHECK_USERNAME_AND_TRY_AGAIN
from utils.constants.states import INSTAGRAM_STATE, YOUTUBE_MP3_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_md, send_md_msg
from utils import keyboards

from yt_dlp import YoutubeDL, utils
from pydub import AudioSegment

# Init logger
logger = getLogger(__name__)

ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': DOWNLOAD_MP3_PATH + '%(title)s.%(ext)s',
    'restrictfilenames': True,
    # 'writethumbnail': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }]
}

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
    # message_is_pinterest = PINTEREST_SEGMENT in message

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    bot_message = await context.bot.send_message(chat_id=update.message.chat_id, text=PROCESSING)

    if message_is_url:
        with YoutubeDL(ydl_opts) as ydl:
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
                text=GETTING_MEDIA_INFORMATION,
            )

            info_dict = ydl.extract_info(message, download=False)
            
            is_playlist = info_dict.get("_type") == PLAYLIST

            # if is_playlist:
            #     downloadin_text = f"Downloading {info_dict.get('title')} ({len(info_dict.get('entries'))} items) ... 😇"
            # else:
            #     downloadin_text = f"Downloading {info_dict.get('title')} ... 😇"
        
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
                text=DOWNLOADING_VIDEO,
            )

            try:
                ydl.download([message])
                
                if is_playlist:
                    await context.bot.deleteMessage(
                        message_id=bot_message.message_id,
                        chat_id=update.message.chat_id,
                    )

                    items = info_dict.get('entries')
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
                                # title=s.json["name"], 
                                # performer=s.json["artist"], 
                                reply_markup=keyboards.pinterest_state_keyboard_rm
                            )
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
                            chat_id=update.message.chat_id,
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
                            # title=s.json["name"], 
                            # performer=s.json["artist"], 
                            reply_markup=keyboards.pinterest_state_keyboard_rm
                        )

            except utils.DownloadError:
                await context.bot.editMessageText(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                    text=SOMETHING_WENT_WRONG,
                )
    # else:
    #     await context.bot.editMessageText(
    #         message_id=bot_message.message_id,
    #         chat_id=update.message.chat_id,
    #         text=escape_md(INVALID_PINTEREST_URL),
    #         parse_mode=ParseMode.MARKDOWN_V2
    #     )
    
    return YOUTUBE_MP3_STATE