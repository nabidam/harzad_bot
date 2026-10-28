"""Tasks view, /tasks and /cancel (UX redesign §8, §2.3).

Tasks is a lightweight chat-native job list — not a queue-management
product, and not a conversation screen: opening it never changes the
input mode and never touches running work.

P0 keeps the history in memory (spec §8.3): the view shows up to 5
active tasks plus up to 5 recent completions/failures, and does not
imply the history survives a restart.
"""
import re
from logging import getLogger

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from utils import copy, keyboards
from utils.ack import answer_callback
from utils.constants.keyboards import (
    TASK_CANCEL_CALLBACK_PATTERN,
    TASK_PAGE_CALLBACK_PATTERN,
    TASK_RETRY_CALLBACK_PATTERN,
)
from utils.constants.states import HOME_STATE
from utils.custom_context import MyContext
from utils.tasks import (
    ACTIVE_PAGE_SIZE,
    CANCEL_REQUESTED,
    RECENT_SHOW,
    TaskRegistry,
    get_registry,
)

# Init logger
logger = getLogger(__name__)


# --- Retry (spec §7.3) ------------------------------------------------------

def _retry_players():
    """kind -> replay function over the task's stored retry_payload.
    Kept as a factory (not a module-level dict) so the flow modules
    are imported lazily: this module is imported very early in the
    app build, and the flows pull in the downloaders' dependencies."""
    from bot.handlers.ai import chat as ai_chat
    from bot.handlers.instagram import download as ig_download
    from bot.handlers.music.spotify import download as spotify_download
    from bot.handlers.pinterest import download as pinterest_download
    from bot.handlers.youtube import download as yt_download
    from bot.handlers.youtube import mp3 as yt_mp3

    async def play_ai(update, context, payload):
        await ai_chat.run_ai_chat(update, context, payload["question"])

    async def play_spotify(update, context, payload):
        for url in payload["urls"]:
            await spotify_download.run_spotify_download(
                update, context, url
            )

    async def play_instagram(update, context, payload):
        for url in payload["urls"]:
            await ig_download.run_instagram_download(
                update, context, url
            )

    async def play_pinterest(update, context, payload):
        for url in payload["urls"]:
            await pinterest_download.run_pinterest_download(
                update, context, url
            )

    async def play_video(update, context, payload):
        for url in payload["urls"]:
            await yt_download.run_video_download(
                update, context, url,
                max_height=payload.get("max_height") or 720,
                kind="youtube_video",
            )

    async def play_tiktok(update, context, payload):
        for url in payload["urls"]:
            await yt_download.run_video_download(
                update, context, url, kind="tiktok"
            )

    async def play_mp3(update, context, payload):
        for url in payload["urls"]:
            await yt_mp3.run_mp3_download(update, context, url)

    return {
        "ai": play_ai,
        "spotify": play_spotify,
        "instagram": play_instagram,
        "pinterest": play_pinterest,
        "youtube_video": play_video,
        "tiktok": play_tiktok,
        "youtube_mp3": play_mp3,
    }


def _active_rows(tasks) -> tuple[str, list[list]]:
    """Build (text, keyboard rows) for a page of active tasks.

    Row i of the keyboard cancels line i of the text (same order, same
    limit) — the pairing the spec's per-row Cancel contract implies.
    """
    lines = []
    rows: list[list] = []
    for task in tasks:
        lines.append(task.row())
        from telegram import InlineKeyboardButton
        button_label = copy.BTN_TASK_CANCEL if task.status != CANCEL_REQUESTED \
            else copy.BTN_TASK_STOPPING
        rows.append([
            InlineKeyboardButton(
                button_label,
                callback_data=f"task:cancel:{task.token}",
            ),
            InlineKeyboardButton(copy.BTN_TASKS, callback_data="nav:tasks"),
        ])
    return "\n\n".join(lines), rows


def _recent_section(registry: TaskRegistry, owner_user_id: int) -> str:
    recent = registry.recent_for(owner_user_id, RECENT_SHOW)
    if not recent:
        return ""
    lines = ["", "<i>Recent (this session only):</i>"]
    lines.extend(t.recent_row() for t in recent)
    return "\n".join(lines)


async def render_tasks(update: Update, context: MyContext, *, page: int = 0) -> None:
    """Send (or, for a button click, edit) the Tasks list.

    Never changes the conversation state.
    """
    assert update.effective_user is not None
    assert update.effective_chat is not None
    registry = get_registry(context)
    owner = update.effective_user.id

    active = registry.active_for(owner)
    rows_text: list[str] = []
    keyboard_rows: list[list] = []

    if not active:
        rows_text.append(copy.TASK_NONE)
        from telegram import InlineKeyboardButton
        keyboard_rows = [
            [InlineKeyboardButton(copy.BTN_SEARCH, callback_data="nav:search"),
             InlineKeyboardButton(copy.BTN_HOME, callback_data="nav:home")]
        ]
    else:
        # A stale page (tasks finished since the button was rendered)
        # falls back to the current list, never an empty message
        # (spec §16: task:page stale behavior).
        pages = (len(active) + ACTIVE_PAGE_SIZE - 1) // ACTIVE_PAGE_SIZE
        page = max(0, min(page, pages - 1))
        start = page * ACTIVE_PAGE_SIZE
        page_tasks = active[start:start + ACTIVE_PAGE_SIZE]
        text, rows = _active_rows(page_tasks)
        rows_text.append(text)
        keyboard_rows.extend(rows)
        if start + ACTIVE_PAGE_SIZE < len(active):
            from telegram import InlineKeyboardButton
            keyboard_rows.append([
                InlineKeyboardButton(
                    copy.BTN_SHOW_MORE, callback_data=f"task:page:{page + 1}"
                ),
                InlineKeyboardButton(copy.BTN_HOME, callback_data="nav:home"),
            ])
        recent = _recent_section(registry, owner)
        if recent:
            rows_text.append(recent)
        from telegram import InlineKeyboardButton
        keyboard_rows.append([InlineKeyboardButton(copy.BTN_HOME, callback_data="nav:home")])

    text = "\n\n".join(rows_text)
    from telegram import InlineKeyboardMarkup
    markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None

    # Prefer editing the clicked message (nav:tasks / task:page); for a
    # command or stray click without an editable message, send fresh.
    cq = update.callback_query
    if cq is not None and cq.message is not None:
        try:
            await cq.message.edit_text(
                text=text, parse_mode=ParseMode.HTML, reply_markup=markup,
            )
            return
        except TelegramError:
            logger.debug("tasks: could not edit in place; sending fresh", exc_info=True)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def on_tasks_callback(update: Update, context: MyContext):
    """`nav:tasks` button: open the task list, keep the input mode."""
    cq = update.callback_query
    if cq is None:
        return None
    await answer_callback(cq)
    await render_tasks(update, context)
    return None


async def on_task_page(update: Update, context: MyContext):
    """`task:page:<n>`: next page of active tasks.

    A stale page (fewer tasks now) falls back to the current list.
    """
    cq = update.callback_query
    if cq is None:
        return None
    match = re.match(TASK_PAGE_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    await answer_callback(cq)
    await render_tasks(update, context, page=int(match.group(1)))
    return None


def _cancel_task(task, update: Update, context: MyContext) -> None:
    """Signal one task (shared by task:cancel button and /cancel).

    Best-effort contract (spec §4.3): the worker confirms when the
    current step is actually interrupted; until then the status reads
    "cancellation requested", never a false "stopped".
    """
    task.cancel_event.set()
    task.touch(status=CANCEL_REQUESTED)


async def on_task_cancel(update: Update, context: MyContext):
    """`task:cancel:<token>` — independent of navigation state (§4.3).

    Ack immediately (no hanging spinner), owner-check the token, then
    signal the worker. Stale/foreign tokens get a toast.
    """
    cq = update.callback_query
    if cq is None:
        return None
    match = re.match(TASK_CANCEL_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    token = match.group(1)

    registry = get_registry(context)
    task = registry.get(token)
    if task is None or not task.is_active():
        await answer_callback(cq, copy.TOAST_TASK_STALE)
        return
    assert update.effective_user is not None
    if task.owner_user_id != update.effective_user.id:
        # private task action: never cancel someone else's work
        await answer_callback(cq, copy.TOAST_TASK_STALE)
        return
    await answer_callback(cq)
    _cancel_task(task, update, context)


async def on_task_retry(update: Update, context: MyContext):
    """`task:retry:<token>` — replay a finished task's stored payload
    (spec §7.2/§7.3): each replayed URL starts a NEW task (new token),
    so a retry never resurrects or duplicates the original one.

    Stale/foreign/active tokens get the stale toast. A task without a
    safe-to-replay payload (e.g. the process restarted) gets the
    restart-expired copy with a fresh entry point — never a guess.
    """
    cq = update.callback_query
    if cq is None:
        return None
    match = re.match(TASK_RETRY_CALLBACK_PATTERN, cq.data or "")
    assert match is not None
    token = match.group(1)

    registry = get_registry(context)
    task = registry.get(token)
    if task is None or task.is_active():
        await answer_callback(cq, copy.TOAST_TASK_STALE)
        return
    assert update.effective_user is not None
    if task.owner_user_id != update.effective_user.id:
        await answer_callback(cq, copy.TOAST_TASK_STALE)
        return

    payload = task.retry_payload
    player = _retry_players().get(task.kind)
    if player is None or not payload:
        await answer_callback(cq)
        await cq.message.reply_text(
            copy.ERROR_RESTART,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.home_tasks_row_keyboard(),
        )
        return

    await answer_callback(cq)
    # Awaiting the replay mirrors on_yt_format: the callback is already
    # answered (no spinner), and PTB processes other updates
    # concurrently while this handler coroutine runs the re-download.
    await player(update, context, payload)
    return None


async def on_tasks_command(update: Update, context: MyContext):
    """/tasks: open the Tasks view (spec §2.3). Works in every mode."""
    await render_tasks(update, context)
    return None


async def on_cancel_command(update: Update, context: MyContext):
    """/cancel (spec §2.3).

    - no active tasks -> say so, with a Home exit
    - exactly one    -> cancel it
    - several        -> open Tasks so the user chooses (no guessing)
    """
    assert update.effective_message is not None
    assert update.effective_user is not None
    assert update.effective_chat is not None
    registry = get_registry(context)
    active = registry.active_for(update.effective_user.id)

    if not active:
        await update.effective_message.reply_text(
            copy.TASK_NONE,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.home_keyboard,
        )
        return None

    if len(active) == 1:
        task = active[0]
        _cancel_task(task, update, context)
        await update.effective_message.reply_text(
            copy.TASK_CANCEL_REQUESTED,
            parse_mode=ParseMode.HTML,
        )
        return None

    await update.effective_message.reply_text(
        copy.TASK_SEVERAL,
        parse_mode=ParseMode.HTML,
    )
    await render_tasks(update, context)
    return None
