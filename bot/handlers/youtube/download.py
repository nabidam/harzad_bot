import os
import validators
import re
from logging import getLogger

from telegram import CopyTextButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD, COPY_LINK_BUTTON
from utils.constants.messages import BOT_ID, DIRECT_VIDEO_LINK, DOWNLOADING_VIDEO, GETTING_MEDIA_INFORMATION, GETTING_PROFILE_INFORMATION, GETTING_STORY_INFORMATION, INVALID_PINTEREST_URL, LINK_IS_INVALID, MEDIA_CAPTION, MEDIA_NOT_FOUND, PROCESSING, SENDING_THUMBNAIL, SENDING_VIDEO, SOMETHING_WENT_WRONG, USER_NOT_FOUND_CHECK_USERNAME_AND_TRY_AGAIN
from utils.constants.states import INSTAGRAM_DOWNLOAD_STATE, INSTAGRAM_STATE, PINTEREST_STATE, YOUTUBE_DOWNLOAD_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.exceptions.instagram import LoginException
from utils.helpers import escape_html, prepare_cover, prepare_link, react_message, send_html_msg
from utils import keyboards

from utils.instagram import get_user_client_instagram
from utils.youtube_dl import YDL_OPTS

from yt_dlp import YoutubeDL, utils
import moviepy as mp

# Init logger
logger = getLogger(__name__)

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
        with YoutubeDL(YDL_OPTS) as ydl:
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
                text=GETTING_MEDIA_INFORMATION,
            )

            try:
                info_dict = ydl.extract_info(message, download=False)
            except utils.DownloadError:
                await react_message(update, context.bot, "👎")
                await context.bot.editMessageText(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                    text=SOMETHING_WENT_WRONG,
                )
                return YOUTUBE_DOWNLOAD_STATE
            
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
                    items = info_dict.get('entries')
                    if not items:
                        await react_message(update, context.bot, "👎")
                        await context.bot.editMessageText(
                            message_id=bot_message.message_id,
                            chat_id=update.message.chat_id,
                            text=SOMETHING_WENT_WRONG,
                        )
                        return YOUTUBE_DOWNLOAD_STATE
                    for index, item in enumerate(items):
                        ydl.process_info(item)
                        title = item.get('title')
                        ext = item.get('ext')
                        filename = ydl.prepare_filename(item)
                        thumbnail_url = item.get("thumbnail")
                        with open(filename, "rb") as f:
                            video = mp.VideoFileClip(filename)
                            duration = video.duration
                            
                            # check for size
                            # Get the file size in bytes
                            file_size = os.path.getsize(filename)

                            # Convert to megabytes (MB)
                            file_size_mb = round(file_size / (1024 * 1024), 2)

                            await context.bot.deleteMessage(
                                message_id=bot_message.message_id,
                                chat_id=update.message.chat_id,
                            )

                            if file_size_mb < 50:
                                await context.bot.send_chat_action(
                                    chat_id=update.effective_message.chat_id,
                                    action=ChatAction.UPLOAD_VIDEO,
                                )
                                # distinct InputFile per field: each needs its
                                # own multipart part; the remote URL is webp,
                                # which Telegram's thumbnail field rejects
                                thumbnail_file = prepare_cover(filename)
                                cover_file = prepare_cover(filename)
                                await context.bot.send_video(
                                    chat_id=update.effective_chat.id, 
                                    video=f, 
                                    duration=duration, 
                                    thumbnail=thumbnail_file if thumbnail_file is not None else thumbnail_url,
                                    cover=cover_file,
                                    caption=MEDIA_CAPTION.format(caption=escape_html(title or ""), bot_id=BOT_ID),
                                    parse_mode=ParseMode.HTML,
                                    protect_content=True,
                                    reply_markup=keyboards.youtube_download_state_keyboard_rm, 
                                    supports_streaming=True
                                )
                                await react_message(update, context.bot, "👍")
                            else:
                                await context.bot.send_chat_action(
                                    chat_id=update.effective_message.chat_id,
                                    action=ChatAction.TYPING,
                                )
                                link = prepare_link(filename)
                                download_text = DIRECT_VIDEO_LINK.format(path=escape_html(link))
                                copy_keyboard = InlineKeyboardMarkup(
                                    keyboards.youtube_download_state_keyboard_rm.inline_keyboard
                                    + [[CopyTextButton(COPY_LINK_BUTTON, data=link)]]
                                )
                                await send_html_msg(context.bot, update.effective_chat.id, download_text, copy_keyboard)
                                await react_message(update, context.bot, "👍")
                else:
                    ydl.process_info(info_dict)
                    title = info_dict.get('title')
                    ext = info_dict.get('ext')
                    filename = ydl.prepare_filename(info_dict)
                    thumbnail_url = info_dict.get("thumbnail")
                    with open(filename, "rb") as f:
                        video = mp.VideoFileClip(filename)
                        duration = video.duration

                        # check for video size
                        # Get the file size in bytes
                        file_size = os.path.getsize(filename)

                        # Convert to megabytes (MB)
                        file_size_mb = round(file_size / (1024 * 1024), 2)

                        await context.bot.deleteMessage(
                            message_id=bot_message.message_id,
                            chat_id=update.message.chat_id,
                        )

                        if file_size_mb < 50:
                            await context.bot.send_chat_action(
                                chat_id=update.effective_message.chat_id,
                                action=ChatAction.UPLOAD_VIDEO,
                            )
                            # distinct InputFile per field: each needs its
                            # own multipart part; the remote URL is webp,
                            # which Telegram's thumbnail field rejects
                            thumbnail_file = prepare_cover(filename)
                            cover_file = prepare_cover(filename)
                            await context.bot.send_video(
                                chat_id=update.effective_chat.id, 
                                video=f, 
                                duration=duration, 
                                thumbnail=thumbnail_file if thumbnail_file is not None else thumbnail_url,
                                cover=cover_file,
                                caption=MEDIA_CAPTION.format(caption=escape_html(title or ""), bot_id=BOT_ID),
                                parse_mode=ParseMode.HTML,
                                protect_content=True,
                                reply_markup=keyboards.youtube_download_state_keyboard_rm, 
                                supports_streaming=True
                            )
                            await react_message(update, context.bot, "👍")
                        else:
                            await context.bot.send_chat_action(
                                chat_id=update.effective_message.chat_id,
                                action=ChatAction.TYPING,
                            )
                            link = prepare_link(filename)
                            download_text = DIRECT_VIDEO_LINK.format(path=escape_html(link))
                            copy_keyboard = InlineKeyboardMarkup(
                                keyboards.youtube_download_state_keyboard_rm.inline_keyboard
                                + [[CopyTextButton(COPY_LINK_BUTTON, data=link)]]
                            )
                            await send_html_msg(context.bot, update.effective_chat.id, download_text, copy_keyboard)
                            await react_message(update, context.bot, "👍")

            except utils.DownloadError:
                await react_message(update, context.bot, "👎")
                await context.bot.editMessageText(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                    text=SOMETHING_WENT_WRONG,
                )
    else:
        await react_message(update, context.bot, "👎")
        await context.bot.editMessageText(
            message_id=bot_message.message_id,
            chat_id=update.message.chat_id,
            text=LINK_IS_INVALID,
        )
    # else:
    #     await context.bot.editMessageText(
    #         message_id=bot_message.message_id,
    #         chat_id=update.message.chat_id,
    #         text=escape_md(INVALID_PINTEREST_URL),
    #         parse_mode=ParseMode.MARKDOWN_V2
    #     )
    
    return YOUTUBE_DOWNLOAD_STATE