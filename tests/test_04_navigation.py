"""#4 bot.navigation — the single navigate() dispatcher.

All Update objects are fakes (MagicMock); no network.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.error import BadRequest
from telegram.constants import ParseMode

from bot.navigation import NAVIGATOR, _hello_text, navigate
from utils.constants.keyboards import CB_AI, CB_AI_CHAT, CB_START, CB_YOUTUBE
from utils.constants.messages import CHOOSE_MESSAGE, YOUTUBE_INSTRUCTION
from utils.constants.states import AI_CHAT_STATE, START_STATE, YOUTUBE_STATE


def make_update(cb_data: str, *, with_message=True, message_edit=None,
                first_name="Nab", last_name=None, username="nab", user_id=777):
    cq = MagicMock()
    cq.data = cb_data
    cq.answer = AsyncMock()
    if with_message:
        cq.message = MagicMock()
        cq.message.message_id = 55
        cq.message.edit_text = (
            message_edit if message_edit is not None else AsyncMock()
        )
    else:
        cq.message = None
    update = MagicMock(spec=Update)
    update.callback_query = cq
    update.effective_chat = MagicMock()
    update.effective_chat.id = 42
    update.effective_user = MagicMock(
        id=user_id, first_name=first_name, last_name=last_name,
        username=username,
    )
    return update, cq


def make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


# --- _hello_text ------------------------------------------------------------

def test_hello_text_full_name_and_username():
    update, _ = make_update(CB_START)
    text = _hello_text(update)
    assert "<b>Nab (@nab)</b>" in text
    assert "<code>777</code>" in text


def test_hello_text_username_only():
    update, _ = make_update(CB_START, first_name=None, username="solo")
    assert "<b>@solo</b>" in _hello_text(update)


def test_hello_text_no_names_falls_back_to_there():
    update, _ = make_update(CB_START, first_name=None, username=None)
    assert "<b>there</b>" in _hello_text(update)


def test_hello_text_escapes_html():
    update, _ = make_update(CB_START, first_name="A<script>", username="x")
    assert "<script>" not in _hello_text(update)


# --- navigate() ---------------------------------------------------------------

async def test_navigate_edits_message_and_returns_state():
    import utils.keyboards as kb

    update, cq = make_update(CB_YOUTUBE)
    result = await navigate(update, make_context())
    assert result == YOUTUBE_STATE
    cq.answer.assert_awaited_once()
    edit = cq.message.edit_text
    edit.assert_awaited_once()
    kwargs = edit.await_args.kwargs
    assert kwargs["text"] == CHOOSE_MESSAGE
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["reply_markup"] is kb.youtube_state_keyboard_rm


async def test_navigate_start_uses_hello_text():
    update, cq = make_update(CB_START)
    result = await navigate(update, make_context())
    assert result == START_STATE
    assert "Nab" in cq.message.edit_text.await_args.kwargs["text"]


async def test_navigate_unknown_callback_returns_none():
    update, cq = make_update("nav:does-not-exist")
    result = await navigate(update, make_context())
    assert result is None
    cq.answer.assert_awaited_once()
    cq.message.edit_text.assert_not_awaited()


async def test_navigate_not_modified_swallow_path():
    update, cq = make_update(
        CB_AI_CHAT, message_edit=AsyncMock(side_effect=BadRequest("Message is not modified"))
    )
    context = make_context()
    result = await navigate(update, context)
    assert result == AI_CHAT_STATE
    context.bot.send_message.assert_not_awaited()


async def test_navigate_uneditable_message_falls_back_to_new_message():
    update, cq = make_update(
        CB_AI, message_edit=AsyncMock(
            side_effect=BadRequest("Can't edit this type of message")
        )
    )
    context = make_context()
    result = await navigate(update, context)
    assert result is not None
    context.bot.send_message.assert_awaited_once()
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["parse_mode"] == ParseMode.HTML


async def test_navigate_without_message_returns_state_only():
    update, cq = make_update(CB_START, with_message=False)
    result = await navigate(update, make_context())
    assert result == START_STATE
    cq.answer.assert_awaited_once()
