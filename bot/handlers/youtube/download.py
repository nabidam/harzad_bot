import os
import validators
from logging import getLogger

from telegram import CopyTextButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD, COPY_LINK_BUTTON
from utils.constants.messages import BOT_ID, DIRECT_VIDEO_LINK, DOWNLOADING_VIDEO, GETTING_MEDIA_INFORMATION, LINK_IS_INVALID, MEDIA_CAPTION, PROCESSING, SOMETHING_WENT_WRONG

from utils.constants.states import INSTAGRAM_STATE, YOUTUBE_DOWNLOAD_STATE

from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html, prepare_cover, prepare_link, react_message, send_html_msg
from utils.storage import cleanup_media
from utils import keyboards

from utils.youtube_dl import video_opts

from mutagen import File as MutagenFile
from yt_dlp import YoutubeDL, utils

# Init logger
logger = getLogger(__name__)


async def run_video_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, max_height: int = 720) -> None:
    """Core YouTube/yt-dlp video flow, shared by the menu state handler
    and the universal link dispatcher (plan 1.1).

    `url` is the video or playlist URL to process. `max_height` caps the
    format selection (the dispatcher offers 720p/360p).
    """
    assert update.effective_chat is not None
    assert update.effective_message is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    bot_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=PROCESSING)

    with YoutubeDL(video_opts(max_height)) as ydl:
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
                for index, item in enumerate(items):
                    ydl.process_info(item)
                    title = item.get('title')
                    ext = item.get('ext')
                    filename = ydl.prepare_filename(item)
                    thumbnail_url = item.get("thumbnail")
                    # duration via mutagen (moviepy was only ever used for this)
                    duration = int(MutagenFile(filename).info.length)
                    with open(filename, "rb") as f:

                        # check for size
                        # Get the file size in bytes
                        file_size = os.path.getsize(filename)

                        # Convert to megabytes (MB)
                        file_size_mb = round(file_size / (1024 * 1024), 2)

                        await context.bot.deleteMessage(
                            message_id=bot_message.message_id,
                            chat_id=update.effective_chat.id,
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
                            # delivered to Telegram: the local file is staging only
                            cleanup_media(filename)
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
                # duration via mutagen (moviepy was only ever used for this)
                duration = int(MutagenFile(filename).info.length)
                with open(filename, "rb") as f:

                    # check for video size
                    # Get the file size in bytes
                    file_size = os.path.getsize(filename)

                    # Convert to megabytes (MB)
                    file_size_mb = round(file_size / (1024 * 1024), 2)

                    await context.bot.deleteMessage(
                        message_id=bot_message.message_id,
                        chat_id=update.effective_chat.id,
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
                        # delivered to Telegram: the local file is staging only
                        cleanup_media(filename)
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
        await run_video_download(update, context, message)
    else:
        await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        bot_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=PROCESSING)
        await react_message(update, context.bot, "👎")
        await context.bot.editMessageText(
            message_id=bot_message.message_id,
            chat_id=update.effective_chat.id,
            text=LINK_IS_INVALID,
        )

    return YOUTUBE_DOWNLOAD_STATE
