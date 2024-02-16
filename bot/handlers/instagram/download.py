import validators
import re
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants import *
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import BOT_ID, GETTING_MEDIA_INFORMATION, GETTING_PROFILE_INFORMATION, GETTING_STORY_INFORMATION, LINK_IS_INVALID, MEDIA_CAPTION, MEDIA_NOT_FOUND, PROCESSING, SENDING_THUMBNAIL, SENDING_VIDEO, SOMETHING_WENT_WRONG, USER_NOT_FOUND_CHECK_USERNAME_AND_TRY_AGAIN
from utils.constants.states import INSTAGRAM_DOWNLOAD_STATE, INSTAGRAM_STATE

from utils.decorators import send_action,sync_user , log_message
from utils.exceptions.instagram import LoginException
from utils.helpers import send_md_msg
from utils import keyboards

from instagrapi.exceptions import MediaNotFound
from instagrapi.exceptions import UnknownError
from instagrapi.exceptions import UserNotFound

from utils.instagram import get_user_client_instagram

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

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    bot_message = await context.bot.send_message(chat_id=update.message.chat_id, text=PROCESSING)
    
    try:
        client = get_user_client_instagram()
    except LoginException:
        await context.bot.deleteMessage(
            message_id=bot_message.message_id,
            chat_id=update.message.chat_id,
        )
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=SOMETHING_WENT_WRONG,
            reply_markup=keyboards.instagram_download_state_keyboard_rm,
        )
        return INSTAGRAM_DOWNLOAD_STATE
    
    if client is None:
        logger.info("client is None")
        await context.bot.deleteMessage(
            message_id=bot_message.message_id,
            chat_id=update.message.chat_id,
        )
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=SOMETHING_WENT_WRONG,
            reply_markup=keyboards.instagram_download_state_keyboard_rm,
        )
        return INSTAGRAM_DOWNLOAD_STATE

        # TODO: check for url validation
        # TODO: check for link type

    if message_is_url:
        is_link_for_post = False
        is_link_for_reel = False
        if POST_SEGMENT in message:
            is_link_for_post = True
        if REEL_SEGMENT in message:
            is_link_for_reel = True
        if STORY_SEGMENT in message:
            media_type = STORY

        try:
            media_pk_from_url = client.media_pk_from_url(message)
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
                text=GETTING_MEDIA_INFORMATION,
            )
            media_info = client.media_info(media_pk_from_url).dict()
            media_type = media_info["media_type"]
            product_type = media_info["product_type"]
        except (MediaNotFound, UnknownError, ValueError):
            if is_link_for_post or is_link_for_reel:
                await context.bot.deleteMessage(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                )
                await context.bot.send_chat_action(
                    chat_id=update.effective_message.chat_id,
                    action=ChatAction.TYPING,
                )
                await context.bot.send_message(
                    chat_id=update.effective_message.chat_id,
                    text=MEDIA_NOT_FOUND,
                    reply_markup=keyboards.instagram_download_state_keyboard_rm,
                )
                return INSTAGRAM_DOWNLOAD_STATE
            else:
                regex = r"(?<=instagram.com\/)[A-Za-z0-9_.]+"
                username = re.findall(regex, message)[0]
                try:
                    user_data = client.user_info_by_username(
                        username).dict()
                except UserNotFound:
                    await context.bot.deleteMessage(
                        message_id=bot_message.message_id,
                        chat_id=update.message.chat_id,
                    )
                    await update.message.reply_text(
                        LINK_IS_INVALID,
                        reply_markup=keyboards.instagram_download_state_keyboard_rm,
                    )
                    return INSTAGRAM_DOWNLOAD_STATE
                await context.bot.deleteMessage(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                )
                user_profile_picture_url = user_data["profile_pic_url_hd"]
                await update.effective_user.send_photo(
                    photo=user_profile_picture_url,
                    reply_markup=keyboards.instagram_download_state_keyboard_rm,
                )
                return INSTAGRAM_DOWNLOAD_STATE

        if media_type == MEDIA_PHOTO:
            await context.bot.deleteMessage(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
            )
            await context.bot.send_chat_action(
                chat_id=update.effective_message.chat_id,
                action=ChatAction.UPLOAD_PHOTO)
            await update.effective_user.send_photo(photo=media_info["thumbnail_url"])
            caption = MEDIA_CAPTION.format(
                    caption=media_info["caption_text"],
                    bot_id=BOT_ID,
                )
            await send_md_msg(context.bot, update.effective_chat.id, caption, keyboards.instagram_download_state_keyboard_rm)
            return INSTAGRAM_DOWNLOAD_STATE
        elif (media_type == MEDIA_VIDEO and product_type == IS_FEED
                or media_type == MEDIA_IGTV and product_type == IS_IGTV
                or media_type == MEDIA_REEL and product_type == IS_CLIPS):
            await context.bot.deleteMessage(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
            )
            await context.bot.send_chat_action(
                chat_id=update.effective_message.chat_id,
                action=ChatAction.UPLOAD_VIDEO)
            await update.effective_user.send_video(video=media_info["video_url"])
            caption = MEDIA_CAPTION.format(
                    caption=media_info["caption_text"],
                    bot_id=BOT_ID,
                )
            await send_md_msg(context.bot, update.effective_chat.id, caption, keyboards.instagram_download_state_keyboard_rm)
            return INSTAGRAM_DOWNLOAD_STATE
        elif media_type == MEDIA_ALBUM:
            await context.bot.deleteMessage(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
            )
            for media in media_info["resources"]:
                if media["video_url"] is not None:
                    await context.bot.send_chat_action(
                        chat_id=update.effective_message.chat_id,
                        action=ChatAction.UPLOAD_VIDEO,
                    )
                    await update.effective_user.send_video(video=media["video_url"])
                else:
                    await context.bot.send_chat_action(
                        chat_id=update.effective_message.chat_id,
                        action=ChatAction.UPLOAD_PHOTO,
                    )
                    await update.effective_user.send_photo(photo=media["thumbnail_url"])
            caption = MEDIA_CAPTION.format(
                    caption=media_info["caption_text"],
                    bot_id=BOT_ID,
                )
            await send_md_msg(context.bot, update.effective_chat.id, caption, keyboards.instagram_download_state_keyboard_rm)
            return INSTAGRAM_DOWNLOAD_STATE
        elif media_type == STORY:
            try:
                story_pk_from_url = client.story_pk_from_url(message)
                await context.bot.editMessageText(
                    chat_id=update.message.chat_id,
                    message_id=bot_message.message_id,
                    text=GETTING_STORY_INFORMATION,
                )
                story_info = client.story_info(story_pk_from_url)

                if story_info.video_url is None:
                    await context.bot.editMessageText(
                        chat_id=update.message.chat_id,
                        message_id=bot_message.message_id,
                        text=SENDING_THUMBNAIL,
                    )
                    await context.bot.send_chat_action(
                        chat_id=update.effective_message.chat_id,
                        action=ChatAction.UPLOAD_PHOTO,
                    )
                    await update.effective_user.send_photo(
                        photo=story_info.thumbnail_url, # type: ignore
                        reply_markup=keyboards.instagram_download_state_keyboard_rm,
                    )
                    await context.bot.deleteMessage(
                        message_id=bot_message.message_id,
                        chat_id=update.message.chat_id,
                    )
                else:
                    await context.bot.editMessageText(
                        chat_id=update.message.chat_id,
                        message_id=bot_message.message_id,
                        text=SENDING_THUMBNAIL,
                    )
                    await context.bot.send_chat_action(
                        chat_id=update.effective_message.chat_id,
                        action=ChatAction.UPLOAD_PHOTO,
                    )
                    await update.effective_user.send_photo(
                        photo=story_info.thumbnail_url, # type: ignore
                        reply_markup=keyboards.instagram_download_state_keyboard_rm,
                    )
                    await context.bot.editMessageText(
                        chat_id=update.message.chat_id,
                        message_id=bot_message.message_id,
                        text=SENDING_VIDEO,
                    )
                    await context.bot.send_chat_action(
                        chat_id=update.effective_message.chat_id,
                        action=ChatAction.UPLOAD_VIDEO,
                    )
                    await update.effective_user.send_video(
                        video=story_info.video_url,
                        reply_markup=keyboards.instagram_download_state_keyboard_rm,
                    )
                    await context.bot.deleteMessage(
                        message_id=bot_message.message_id,
                        chat_id=update.message.chat_id,
                    )
                return INSTAGRAM_DOWNLOAD_STATE
            except MediaNotFound:
                await context.bot.editMessageText(
                    chat_id=update.message.chat_id,
                    message_id=bot_message.message_id,
                    text=MEDIA_NOT_FOUND,
                    reply_markup=keyboards.instagram_download_state_keyboard_rm, # type: ignore
                )
                return INSTAGRAM_DOWNLOAD_STATE
        else:
            await update.message.reply_text(LINK_IS_INVALID, reply_markup=keyboards.back_keyboard) # type: ignore
            return INSTAGRAM_DOWNLOAD_STATE
        
    elif message.startswith("@"):
        username = message.split("@")[1]
        try:
            await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
                text=GETTING_PROFILE_INFORMATION,
            )
            user_data = client.user_info_by_username(username).dict()
            await context.bot.deleteMessage(
                message_id=bot_message.message_id,
                chat_id=update.message.chat_id,
            )
            await context.bot.send_chat_action(
                chat_id=update.effective_message.chat_id,
                action=ChatAction.UPLOAD_PHOTO)
            user_profile_picture_url = user_data["profile_pic_url_hd"]
            await update.effective_user.send_photo(
                photo=user_profile_picture_url, reply_markup=keyboards.instagram_download_state_keyboard_rm)
            return INSTAGRAM_DOWNLOAD_STATE
        except UserNotFound:
            await context.bot.deleteMessage(message_id=bot_message.message_id,
                                            chat_id=update.message.chat_id)
            await update.message.reply_text(
                USER_NOT_FOUND_CHECK_USERNAME_AND_TRY_AGAIN,
                reply_markup=keyboards.back_keyboard) # type: ignore
    else:
        await update.message.reply_text(LINK_IS_INVALID, reply_markup=keyboards.back_keyboard) # type: ignore
    
    message = "This is the image"
    await send_md_msg(context.bot, update.effective_chat.id, message, keyboards.instagram_state_keyboard_rm)
    return INSTAGRAM_DOWNLOAD_STATE
