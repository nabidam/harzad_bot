import asyncio
import validators
from concurrent.futures import ThreadPoolExecutor
import os

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.messages import GETTING_MEDIA_INFORMATION, PROCESSING, SPOTIFY_DOWNLOAD_PROGRESS, TASK_DONE
from utils.constants.states import MUSIC_SPOTIFY_STATE, START_STATE
from configurations.settings import DOWNLOAD_MP3_PATH

from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils import keyboards
from utils.helpers import react_message
from utils.storage import cleanup_media
from spotdl import Spotdl


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


async def run_spotify_download(update: Update, context: MyContext, url: str) -> None:
    """Core Spotify URL download (spotdl), shared by the menu state handler
    and the universal link dispatcher (plan 1.1).

    `url` may be a track, album or playlist link; spotdl resolves it.
    """
    assert update.effective_chat is not None
    assert update.effective_message is not None

    spotdl_instance = context.application.bot_data["spotdl_instance"]

    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
    bot_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=PROCESSING)
    await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.effective_chat.id,
                text=GETTING_MEDIA_INFORMATION,
            )
    songs = await await_search_from_spotify(spotdl_instance, url)

    for index, song in enumerate(songs):
        download_progress_text = SPOTIFY_DOWNLOAD_PROGRESS.format(current=index+1, total=len(songs))
        await context.bot.editMessageText(
                message_id=bot_message.message_id,
                chat_id=update.effective_chat.id,
                text=download_progress_text,
            )

        s, p = await await_download_from_spotify(spotdl_instance, song)

        try:
            assert p is not None
            assert s.cover_url is not None
            mp3 = open(os.path.join(DOWNLOAD_MP3_PATH, p.name), 'rb')
            await context.bot.send_audio(
                chat_id=update.effective_chat.id, 
                audio=mp3, 
                thumbnail=s.cover_url, 
                title=s.json["name"], 
                performer=s.json["artist"], 
                duration=int(s.duration/1000),
                protect_content=True,
            )
            # delivered to Telegram: the local file is staging only
            cleanup_media(os.path.join(DOWNLOAD_MP3_PATH, p.name))
        except AssertionError:
            pass

    await context.bot.deleteMessage(
        message_id=bot_message.message_id,
        chat_id=update.effective_chat.id,
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=TASK_DONE,
        reply_markup=keyboards.music_spotify_state_keyboard_rm,
    )
    await react_message(update, context.bot, "👍")


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
        await run_spotify_download(update, context, message)
    # else: plain text — no URL given. Phase 1.2 turns this into a search.

    return MUSIC_SPOTIFY_STATE
