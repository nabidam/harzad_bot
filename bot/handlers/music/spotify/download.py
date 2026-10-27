import asyncio
import validators
from concurrent.futures import ThreadPoolExecutor
from logging import getLogger
import os

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from connectors import spotdl
from connectors.spotdl import create_spotdl_instance
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import GETTING_MEDIA_INFORMATION, PROCESSING, SPOTIFY_DOWNLOAD_PROGRESS, TASK_DONE
from utils.constants.states import MUSIC_SPOTIFY_STATE, START_STATE
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID, DOWNLOAD_MP3_PATH

from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils import keyboards
from utils.helpers import react_message
# from utils.Spotidl import Spotidl
from spotdl import Spotdl

# Init logger
# logger = getLogger(__name__)

def search_term(spotdl: Spotdl, term: str):
    songs = spotdl.search([term])

    return songs

async def await_search_from_spotify(spotdl: Spotdl, term: str):
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, search_term, spotdl, term)
    return result

async def await_download_from_spotify(spotdl: Spotdl, song):
    """Download one song by awaiting spotdl's async core on the running loop.

    Never call spotdl's sync API (`spotdl.download`) from the bot: spotdl's
    Downloader.__init__ does asyncio.set_event_loop(its own fresh loop) in
    the main thread, and PTB's Application.__run picks that same loop up via
    asyncio.get_event_loop() and runs the whole bot on it. The sync API then
    calls loop.run_until_complete() on an already-running loop, raising
    RuntimeError("This event loop is already running"). Worse, the
    asyncio.gather() inside download_multiple_songs() schedules the download
    task on the running loop *before* run_until_complete() checks, so the
    song still downloads as an orphaned task: the mp3 lands on disk but the
    (already dead) handler never sends it. (Incident: 2026-10-27 10:03.)

    pool_download is spotdl's own per-song unit (semaphore +
    async_search_and_download), and async_search_and_download offloads every
    slow sync step (lyrics, yt-dlp search/download) to the default executor,
    so awaiting it on the bot loop does not block polling.
    """
    return await spotdl.downloader.pool_download(song)


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: MyContext):
    assert update.message is not None
    message = update.message.text
    print(message)
    if message == BACK_KEYBOARD:
        await update.message.reply_text("backed", reply_markup=keyboards.start_keyboard_rm)
        return START_STATE

    assert message is not None
    message_is_url = validators.url(message) # type: ignore

    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None

    if message_is_url:
        # spotdl_instance = context.spotdl
        spotdl_instance = context.application.bot_data["spotdl_instance"]
        
        await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        bot_message = await context.bot.send_message(chat_id=update.message.chat_id, text=PROCESSING)
        await context.bot.editMessageText(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                    text=GETTING_MEDIA_INFORMATION,
                )
        songs = await await_search_from_spotify(spotdl_instance, message)

        for index, song in enumerate(songs):
            download_progress_text = SPOTIFY_DOWNLOAD_PROGRESS.format(current=index+1, total=len(songs))
            await context.bot.editMessageText(
                    message_id=bot_message.message_id,
                    chat_id=update.message.chat_id,
                    text=download_progress_text,
                )
            
            s, p = await await_download_from_spotify(spotdl_instance, song)

            try:
                assert p is not None
                assert s.cover_url is not None
                mp3 = open(os.path.join(DOWNLOAD_MP3_PATH, p.name), 'rb')
                # caption = p.parts[-1].split(".")[0]
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id, 
                    audio=mp3, 
                    thumbnail=s.cover_url, 
                    title=s.json["name"], 
                    performer=s.json["artist"], 
                    duration=int(s.duration/1000),
                    protect_content=True,
                )
            except AssertionError:
                pass
        
        await context.bot.deleteMessage(
            message_id=bot_message.message_id,
            chat_id=update.message.chat_id,
        )
        
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=TASK_DONE,
            reply_markup=keyboards.music_spotify_state_keyboard_rm,
        )
        await react_message(update, context.bot, "👍")
        
        # songs = await await_download_from_spotify(message, update, context)
    # songs = spotdl.search([message])

    # # try:
    # #     loop = asyncio.get_event_loop()
    # # except RuntimeError:
    # #     loop = asyncio.new_event_loop()
    # #     asyncio.set_event_loop(loop)
            
    # # task = loop.create_task(user_insert_events(target))
    # # if not loop.is_running():
    # #     loop.run_until_complete(task)
    # print(songs)
    # # results = spotdl.download_songs(songs)
    # song, path = spotdl.download(songs[0])

    # for song, path in songs:
    #     assert path is not None
    #     # print("song: ", song)
    #     print("path: ", path.name)
    #     caption = path.parts[-1].split(".")[0]
        
    #     assert update.effective_chat is not None
    #     message = path
    #     mp3 = open(path.name, 'rb')
    #     caption = path.parts[-1].split(".")[0]
    #     await context.bot.send_audio(chat_id=update.effective_chat.id, audio=mp3, caption=caption, reply_markup=keyboards.music_spotify_state_keyboard_rm)
    #     await send_md_msg(context.bot, update.effective_chat.id, message, keyboards.music_spotify_state_keyboard_rm)
    return MUSIC_SPOTIFY_STATE
