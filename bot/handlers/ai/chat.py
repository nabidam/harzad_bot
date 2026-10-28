"""AI chat (UX redesign §9) — one question in, one answer out.

Each message is answered independently: the current backend has no
conversation memory, and the prompt says so (spec §2.2). A request is
a TASK (spec §4.1): registered in the TaskRegistry from the first
second, one status message shows the honest thinking phase on the
shared status engine, [⏹ Stop] is a real `task:cancel:<token>`
cancel, and the timeout card's Retry replays the stored question
under a new task token (spec §7.3).

The LLM call is async (langchain's ainvoke runs the HTTP request on
the loop, so it can be cancelled), and the 30 s guard (spec §9
proposed value — telemetry should validate it) is a plain timeout on
the awaited call. Stopping aborts the in-flight request best-effort;
reliable stop, streaming, and memory are P1/P2 backend work, and the
copy never claims more than that.
"""
import asyncio
from logging import getLogger

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError

from configurations.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from utils import copy, keyboards, progress
from utils.constants.states import AI_CHAT_STATE
from utils.custom_context import MyContext
from utils.decorators import send_action, sync_user, log_message
from utils.helpers import escape_html
from utils.tasks import CANCELLED, DONE, FAILED, get_registry

from bot.handlers import common
from langchain_openai import ChatOpenAI

# Init logger
logger = getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Harzad, a friendly Telegram media-download bot. "
    "Reply to the user's message in a friendly, conversational tone. "
    "Keep answers short (a few sentences) and use plain text only."
)

# Any OpenAI-compatible server (vLLM, Ollama, LM Studio, ...) — see settings.
llm = ChatOpenAI(
    model=LLM_MODEL,
    openai_api_base=LLM_BASE_URL,
    openai_api_key=LLM_API_KEY,
)

# Spec §9 status thresholds (fixed for AI chat, not the media stages).
WORKING_AT_SECONDS = 5
SLOW_AT_SECONDS = 15
# Spec §9 proposed 30 s answer timeout (owner decision: the
# recommended default applies; production telemetry should validate
# it before it is treated as a measured fact).
ANSWER_TIMEOUT_SECONDS = 30


async def _set_status(status, text: str, markup=None) -> None:
    """Best-effort in-place status edit (the user may have deleted it)."""
    try:
        await status.edit_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    except TelegramError:
        logger.warning("could not edit the AI status message", exc_info=True)


def _abort(call: asyncio.Task) -> None:
    """Best-effort abort of an in-flight answer request (spec §9:
    reliable stop is P1/P2). The task is awaited on the loop, so
    cancelling it aborts the HTTP request; a backend that swallows
    the cancellation just keeps running detached, and the copy never
    claims otherwise. The exception (if any) is retrieved so a late
    failure is logged, not surfaced as an un-retrieved task error."""
    def _retrieve(t: asyncio.Task) -> None:
        if not t.cancelled():
            exc = t.exception()
            if exc is not None:
                logger.warning("aborted AI request failed late", exc_info=exc)

    call.add_done_callback(_retrieve)
    call.cancel()


async def run_ai_chat(update: Update, context: MyContext, question: str) -> None:
    """Core AI chat flow (spec §9), shared by the AI input screen and
    retry replay. Undecorated core (like the media flows): the
    callers carry their own chat action and DB work."""
    assert update.effective_chat is not None
    assert update.effective_message is not None
    assert update.effective_user is not None
    chat_id = update.effective_chat.id

    registry = get_registry(context)
    task = registry.create(
        owner_user_id=update.effective_user.id,
        chat_id=chat_id,
        kind="ai",
        input=question,
        retry_payload={"question": question},
    )
    task.touch(phase="resolving")

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=copy.AI_THINKING,
        reply_markup=keyboards.ai_stop_keyboard(task.token),
    )
    task.touch(status_message_id=status.message_id)

    stages = [
        (WORKING_AT_SECONDS,
         lambda e: copy.AI_WORKING.format(elapsed=e)),
        (SLOW_AT_SECONDS,
         lambda e: copy.AI_SLOW.format(elapsed=e)),
    ]

    call = asyncio.create_task(
        llm.ainvoke([("system", SYSTEM_PROMPT), ("user", question)])
    )
    timeout_task = asyncio.create_task(
        asyncio.sleep(ANSWER_TIMEOUT_SECONDS)
    )
    cancel_watch = asyncio.create_task(task.cancel_event.wait())
    timed_out = False
    cancelled = False
    answer = None
    error = None
    try:
        async with progress.status_ticker(
            context.bot, chat_id, status.message_id,
            stages=stages, parse_mode=ParseMode.HTML,
        ):
            done, _ = await asyncio.wait(
                {call, timeout_task, cancel_watch},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if call in done:
                if task.cancel_event.is_set():
                    # Stop won the race: the answer arrived too late —
                    # discard it, never send a post-cancel answer
                    cancelled = True
                else:
                    try:
                        answer = call.result()
                    except Exception:
                        logger.warning("AI request failed", exc_info=True)
                        error = "llm_error"
            elif timeout_task in done:
                # The §9 guard: abort the request and show the honest
                # timeout card — never a claim the backend stopped
                timed_out = True
                _abort(call)
            else:
                cancelled = True
                await _set_status(
                    status, copy.TASK_CANCEL_REQUESTED,
                    keyboards.home_tasks_row_keyboard(),
                )
                task.touch(phase="cancelling")
                _abort(call)
    finally:
        timeout_task.cancel()
        if not cancel_watch.done():
            cancel_watch.cancel()

    # --- finalize: the status message becomes the final answer --------
    if cancelled:
        await _set_status(
            status, copy.TASK_AI_CANCELLED,
            keyboards.home_tasks_row_keyboard(),
        )
        registry.finish(task.token, CANCELLED, note="stopped")
        return

    if timed_out:
        await _set_status(
            status, copy.ERROR_AI_TIMEOUT,
            keyboards.retry_home_keyboard(task.token),
        )
        registry.finish(task.token, FAILED, note="timed_out")
        return

    if error is not None:
        # the LLM backend itself failed: an internal error, retryable
        # (the question is stored on the task)
        await _set_status(
            status, copy.ERROR_UNEXPECTED,
            keyboards.retry_home_keyboard(task.token),
        )
        registry.finish(task.token, FAILED, note="llm error")
        return

    # success: the answer goes out as a NEW message; the status is
    # removed (spec §9)
    content = getattr(answer, "content", "") or ""
    if not content.strip():
        # an empty completion is a backend failure, not an answer
        # (Telegram would reject the empty send)
        await _set_status(
            status, copy.ERROR_UNEXPECTED,
            keyboards.retry_home_keyboard(task.token),
        )
        registry.finish(task.token, FAILED, note="empty answer")
        return
    try:
        await status.delete()
    except TelegramError:
        logger.warning("could not delete the AI status message",
                       exc_info=True)
    await context.bot.send_message(
        chat_id=chat_id,
        text=escape_html(content),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboards.ai_answer_keyboard(),
    )
    registry.finish(task.token, DONE, note="answered")


@send_action(ChatAction.TYPING)
@sync_user
@log_message
async def handler(update: Update, context: MyContext):
    """TEXT entry for the AI Chat input screen (spec §2.2, §9).

    Supported media links never reach the LLM (the universal
    dispatcher runs first in every state); unsupported URLs get the
    supported-platforms error (shared precheck), never a prompt
    injection into the model.
    """
    assert update.message is not None
    text = (update.message.text or "").strip()

    if await common.precheck(update, context):
        return AI_CHAT_STATE

    if not text:
        await common.reprompt(update, context, copy.AI_PROMPT)
        return AI_CHAT_STATE

    await run_ai_chat(update, context, text)
    return AI_CHAT_STATE
