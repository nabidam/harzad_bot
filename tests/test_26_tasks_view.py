"""#26 Tasks view + task-callback authorization (redesign §8, §2.3, §11.2,
§16).

Pins the group-safety and stale-action contract for the task surface
that had no behavior coverage yet:

- `task:cancel:<token>` and `task:retry:<token>` are SERVER-SIDE
  owner-checked (spec §16 invariant): a foreign member tapping a shared
  card's cancel/retry gets the stale toast and nothing is signalled or
  replayed; an unknown token (process restart) toasts the same
- a terminal task with no stored payload gets the restart-expired card
  with a fresh entry point, never a guess (spec §7.3)
- the Tasks view is scoped to the tapping user: another member's
  running tasks and recent history are never rendered for them
  (spec §11.2)
- pagination: 5 active per page, a stale page (tasks finished since the
  button was rendered) falls back to the current list, never an empty
  message (spec §16: task:page stale behavior)
- /cancel: none -> say so with Home; one -> cancel it; several -> open
  Tasks so the user chooses (no guessing)
"""
from unittest.mock import AsyncMock, MagicMock

import bot.handlers.tasks as tasks
from utils import copy, keyboards
from utils.tasks import CANCELLED, CANCEL_REQUESTED, DONE, FAILED, get_registry

OWNER_A = 9_500_000_001
OWNER_B = 9_500_000_002


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.application.bot_data = {}
    context.bot = MagicMock()
    for m in ("send_chat_action", "set_message_reaction"):
        setattr(context.bot, m, AsyncMock())
    context.bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=500, delete=AsyncMock())
    )
    return context


def make_command_update(text, tg_id=OWNER_A, chat_id=7):
    """A plain message (command) update: no callback_query, so render
    paths that would edit send fresh instead."""
    update = MagicMock()
    msg = MagicMock(chat_id=chat_id, message_id=100, text=text,
                    reply_text=AsyncMock())
    update.message = msg
    update.effective_message = msg
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    update.callback_query = None
    return update


def make_callback(data, tg_id=OWNER_A, chat_id=7):
    update = MagicMock()
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    cq.message = MagicMock(chat_id=chat_id, message_id=600,
                           photo=None, edit_text=AsyncMock(),
                           reply_text=AsyncMock())
    update.callback_query = cq
    update.effective_message = cq.message
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=tg_id, first_name="F")
    return update


async def test_task_cancel_unknown_token_toasts():
    update = make_callback("task:cancel:deadbeef")
    context = make_context()

    await tasks.on_task_cancel(update, context)

    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TASK_STALE
    )
    assert len(get_registry(context)) == 0


async def test_task_cancel_foreign_owner_toasts_and_signals_nothing():
    """Server-side authorization (spec §16): a group member may not
    cancel someone else's task by tapping a shared card."""
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video", input="u"
    )

    update = make_callback(f"task:cancel:{task.token}", tg_id=OWNER_B)
    await tasks.on_task_cancel(update, context)

    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TASK_STALE
    )
    assert not task.cancel_event.is_set()
    assert task.status != CANCEL_REQUESTED


async def test_task_cancel_terminal_task_toasts():
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video", input="u"
    )
    registry.finish(task.token, DONE, note="sent")

    update = make_callback(f"task:cancel:{task.token}")
    await tasks.on_task_cancel(update, context)

    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TASK_STALE
    )
    assert not task.cancel_event.is_set()


async def test_task_cancel_own_active_task_signals():
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video", input="u"
    )

    update = make_callback(f"task:cancel:{task.token}")
    await tasks.on_task_cancel(update, context)

    assert update.callback_query.answer.await_args.args == ()  # plain ack
    assert task.cancel_event.is_set()
    assert task.status == CANCEL_REQUESTED


async def test_task_retry_foreign_owner_toasts_and_replays_nothing(monkeypatch):
    """Same invariant for Retry: replaying another member's stored
    payload is refused (it would burn their request / their chat)."""
    monkeypatch.setattr(tasks, "_retry_players", lambda: {})
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video", input="u",
        retry_payload={"urls": ["u"]},
    )
    registry.finish(task.token, FAILED, note="failed")

    update = make_callback(f"task:retry:{task.token}", tg_id=OWNER_B)
    await tasks.on_task_retry(update, context)

    assert (
        update.callback_query.answer.await_args.args[0]
        == copy.TOAST_TASK_STALE
    )
    update.callback_query.message.reply_text.assert_not_awaited()


async def test_task_retry_without_payload_gets_restart_card():
    """A terminal task with no stored payload (spec §7.3) gets the
    restart-expired copy and a fresh entry point — never a guess."""
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video", input="u",
        retry_payload=None,
    )
    registry.finish(task.token, FAILED, note="failed")

    update = make_callback(f"task:retry:{task.token}")
    await tasks.on_task_retry(update, context)

    assert update.callback_query.answer.await_args.args == ()
    reply = update.callback_query.message.reply_text
    reply.assert_awaited_once()
    assert reply.await_args.args[0] == copy.ERROR_RESTART
    row = reply.await_args.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == ["nav:home", "nav:tasks"]


async def test_tasks_view_empty():
    """No tasks: the honest empty line with a [Search][Home] exit."""
    context = make_context()
    update = make_command_update("/tasks")

    await tasks.on_tasks_command(update, context)

    sent = context.bot.send_message.await_args.kwargs
    assert sent["text"] == copy.TASK_NONE
    row = sent["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == ["nav:search", "nav:home"]


async def test_tasks_view_is_scoped_to_the_owner():
    """Spec §11.2: never show one user's active tasks or recent history
    to another member. The registry is process-wide; the VIEW is not."""
    context = make_context()
    registry = get_registry(context)
    secret_a = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
        input="https://youtu.be/secret-a", title="SecretA",
    )
    recent_a = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
        input="https://youtu.be/old-a", title="OldA",
    )
    registry.finish(recent_a.token, DONE, note="sent")
    active_b = registry.create(
        owner_user_id=OWNER_B, chat_id=8, kind="spotify",
        input="https://open.spotify.com/track/b", title="ActiveB",
    )
    recent_b = registry.create(
        owner_user_id=OWNER_B, chat_id=8, kind="spotify",
        input="https://open.spotify.com/track/oldb", title="OldB",
    )
    registry.finish(recent_b.token, DONE, note="sent")

    # B's view: only B's active task and B's recent
    update_b = make_command_update("/tasks", tg_id=OWNER_B)
    await tasks.render_tasks(update_b, context)
    text_b = context.bot.send_message.await_args.kwargs["text"]
    assert "ActiveB" in text_b
    assert "OldB" in text_b
    assert "SecretA" not in text_b
    assert "OldA" not in text_b

    # A's view: only A's — and the CANCEL row pairs A's task token
    update_a = make_command_update("/tasks", tg_id=OWNER_A)
    await tasks.render_tasks(update_a, context)
    sent_a = context.bot.send_message.await_args.kwargs
    assert "SecretA" in sent_a["text"]
    cancel_row = sent_a["reply_markup"].inline_keyboard[0]
    assert cancel_row[0].callback_data == f"task:cancel:{secret_a.token}"


async def test_tasks_view_shows_recent_section():
    context = make_context()
    registry = get_registry(context)
    active = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
        input="u", title="ActiveOne",
    )
    active.touch(phase="downloading")
    done = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
        input="u2", title="DoneOne",
    )
    registry.finish(done.token, DONE, note="sent")

    await tasks.render_tasks(
        make_command_update("/tasks"), context
    )
    text = context.bot.send_message.await_args.kwargs["text"]
    assert "Recent (this session only):" in text
    assert f"✅ DoneOne — sent" in text
    # the active row carries the label, subject and phase
    assert "<b>Downloading:</b> ActiveOne" in text
    assert "downloading" in text


async def test_tasks_view_pagination_and_stale_page():
    """7 active -> 5 per page + [Show more]; page 2 shows the remaining
    two with no further Show more."""
    context = make_context()
    registry = get_registry(context)
    for i in range(7):
        registry.create(
            owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
            input=f"url-{i}", title=f"T{i}",
        )

    await tasks.render_tasks(make_command_update("/tasks"), context)
    sent = context.bot.send_message.await_args.kwargs
    assert sent["text"].count("<b>") == 5
    # the Show-more row sits above the trailing Home row
    show_more_row = sent["reply_markup"].inline_keyboard[-2]
    assert [b.callback_data for b in show_more_row] == [
        "task:page:1", "nav:home"]

    # page 2: the remaining two, no further Show more
    update = make_callback("task:page:1")
    await tasks.on_task_page(update, context)
    edited = update.callback_query.message.edit_text
    edited.assert_awaited_once()
    text = edited.await_args.kwargs["text"]
    assert text.count("<b>") == 2
    page_buttons = [
        b for row in edited.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row if b.callback_data.startswith("task:page:")
    ]
    assert page_buttons == []


async def test_tasks_view_stale_page_falls_back_to_current_list():
    """The P0 'stale page falls back to current Tasks view' contract:
    7 tasks were rendered with a Show-more button; 5 of them finished;
    the now-stale page still shows the CURRENT two tasks, not empty."""
    context = make_context()
    registry = get_registry(context)
    created = [
        registry.create(
            owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
            input=f"url-{i}", title=f"T{i}",
        )
        for i in range(7)
    ]
    for t in created[:5]:
        registry.finish(t.token, DONE, note="sent")

    # a deep stale page (far beyond what exists now)
    update = make_callback("task:page:9")
    await tasks.on_task_page(update, context)

    edited = update.callback_query.message.edit_text
    edited.assert_awaited_once()
    text = edited.await_args.kwargs["text"]
    assert text.count("<b>") == 2  # the two still-active tasks
    assert text.strip() != ""
    markup = edited.await_args.kwargs["reply_markup"]
    page_buttons = [
        b for row in markup.inline_keyboard for b in row
        if b.callback_data.startswith("task:page:")
    ]
    assert page_buttons == []  # nothing left to page through


async def test_tasks_callback_edits_in_place():
    """nav:tasks on a clickable message edits that message (no fresh
    message in the chat) and keeps the conversation state."""
    context = make_context()
    update = make_callback("nav:tasks")

    result = await tasks.on_tasks_callback(update, context)

    assert result is None
    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.edit_text.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


async def test_cancel_command_no_tasks_says_so_with_home():
    context = make_context()
    update = make_command_update("/cancel")

    await tasks.on_cancel_command(update, context)

    reply = update.effective_message.reply_text
    reply.assert_awaited_once()
    assert reply.await_args.args[0] == copy.TASK_NONE
    assert reply.await_args.kwargs["reply_markup"] is keyboards.home_keyboard


async def test_cancel_command_single_task_signals_it():
    context = make_context()
    registry = get_registry(context)
    task = registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video", input="u"
    )
    update = make_command_update("/cancel")

    await tasks.on_cancel_command(update, context)

    assert task.cancel_event.is_set()
    assert task.status == CANCEL_REQUESTED
    reply = update.effective_message.reply_text
    reply.assert_awaited_once()
    assert reply.await_args.args[0] == copy.TASK_CANCEL_REQUESTED


async def test_cancel_command_several_tasks_opens_tasks_view():
    context = make_context()
    registry = get_registry(context)
    registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="youtube_video",
        input="u1", title="One",
    )
    registry.create(
        owner_user_id=OWNER_A, chat_id=7, kind="spotify",
        input="u2", title="Two",
    )
    update = make_command_update("/cancel")

    await tasks.on_cancel_command(update, context)

    # first the picker hint, then the rendered list
    reply = update.effective_message.reply_text
    assert reply.await_args_list[0].args[0] == copy.TASK_SEVERAL
    sent = context.bot.send_message.await_args.kwargs
    assert sent["text"].count("<b>") == 2
    # neither task was cancelled: /cancel guessed nothing
    assert all(not t.cancel_event.is_set()
               for t in registry._tasks.values())


# --- stale callback answers (spec §16, incident 2026-10-28 21:40) ------------------

STALE_QUERY = (
    "Query is too old and response timeout expired or query id is invalid"
)


async def test_stale_answer_never_raises():
    """A tap delivered after Telegram's ~10 s answer window cannot be
    answered: the BadRequest is swallowed (a property of the tap's
    age, not of the action) and the caller proceeds."""
    from telegram.error import BadRequest

    from utils import ack

    cq = MagicMock()
    cq.answer = AsyncMock(side_effect=BadRequest(STALE_QUERY))

    assert await ack.answer_callback(cq) is False
    assert await ack.answer_callback(cq, "toast") is False


async def test_fresh_answer_still_goes():
    from utils import ack

    cq = MagicMock()
    cq.answer = AsyncMock()

    assert await ack.answer_callback(cq, "hi") is True
    cq.answer.assert_awaited_once_with("hi")


async def test_stale_nav_tasks_tap_still_renders_view():
    """The 2026-10-28 21:40 incident: two 📥 Tasks taps queued behind
    an 88 s download handler were processed after the answer window.
    The unguarded cq.answer() raised BadRequest into the class-6 error
    handler and the user got two "Something went wrong" cards. A stale
    tap now still renders the Tasks view and nothing else."""
    from telegram.error import BadRequest

    context = make_context()
    update = make_callback("nav:tasks")
    update.callback_query.answer = AsyncMock(
        side_effect=BadRequest(STALE_QUERY))

    await tasks.on_tasks_callback(update, context)  # must not raise

    update.callback_query.message.edit_text.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()
