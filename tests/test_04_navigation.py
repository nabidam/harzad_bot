"""#4 bot.navigation — the single navigate() dispatcher (redesign §2, §16).

All Update objects are fakes (MagicMock); no network.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.error import BadRequest
from telegram.constants import ParseMode

from bot.navigation import (
    NAVIGATOR,
    home_text,
    navigate,
    on_ai_tti_stale,
    on_back,
)
from utils import copy
from utils.constants.keyboards import (
    CB_AI,
    CB_AI_CHAT,
    CB_AI_TTI,
    CB_HOME,
    CB_MUSIC,
    CB_START,
    CB_YOUTUBE,
)
from utils.constants.states import (
    AI_CHAT_STATE,
    HOME_STATE,
    SEARCH_INPUT_STATE,
    YOUTUBE_INPUT_STATE,
)


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


# --- home_text (spec §2.1: first name only, no user ID, no bot ID) -------------

def test_home_text_uses_first_name_only():
    update, _ = make_update(CB_START)
    text = home_text(update)
    assert "Hi, Nab" in text
    assert "777" not in text  # no numeric user ID
    assert "@nab" not in text  # no username either
    assert "paste a link" in text.lower() or "link" in text.lower()
    assert "song" in text.lower()


def test_home_text_no_first_name_falls_back_to_there():
    update, _ = make_update(CB_START, first_name=None)
    assert "there" in home_text(update)


def test_home_text_escapes_html():
    update, _ = make_update(CB_START, first_name="A<script>")
    assert "<script>" not in home_text(update)


# --- navigate() ----------------------------------------------------------------

def test_navigator_covers_new_and_legacy_callbacks():
    for cb in (
        CB_START, CB_HOME, CB_MUSIC, CB_AI, CB_AI_CHAT, CB_YOUTUBE,
    ):
        assert cb in NAVIGATOR
    # the retired TTI button is NOT a navigation destination
    assert CB_AI_TTI not in NAVIGATOR


async def test_navigate_youtube_goes_straight_to_input_prompt():
    import utils.keyboards as kb

    update, cq = make_update(CB_YOUTUBE)
    result = await navigate(update, make_context())
    assert result == YOUTUBE_INPUT_STATE
    cq.answer.assert_awaited_once()
    edit = cq.message.edit_text
    edit.assert_awaited_once()
    kwargs = edit.await_args.kwargs
    assert kwargs["text"] == copy.YOUTUBE_PROMPT
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["reply_markup"] is kb.youtube_input_keyboard


async def test_navigate_music_alias_opens_search_input():
    update, cq = make_update(CB_MUSIC)
    result = await navigate(update, make_context())
    assert result == SEARCH_INPUT_STATE
    assert cq.message.edit_text.await_args.kwargs["text"] == copy.SEARCH_PROMPT


async def test_navigate_home_uses_home_text():
    update, cq = make_update(CB_HOME)
    result = await navigate(update, make_context())
    assert result == HOME_STATE
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
    assert result == HOME_STATE
    cq.answer.assert_awaited_once()


# --- back mapping (spec §2.4: every parent is Home) -----------------------------

async def test_back_goes_home_for_every_screen():
    update, cq = make_update("nav:back:youtube")
    result = await on_back(update, make_context())
    assert result == HOME_STATE
    assert "Nab" in cq.message.edit_text.await_args.kwargs["text"]


async def test_back_unknown_screen_still_goes_home():
    update, _ = make_update("nav:back:something-new")
    assert await on_back(update, make_context()) == HOME_STATE


# --- retired TTI button (spec §10) ----------------------------------------------

async def test_ai_tti_stale_gets_toast_not_navigation():
    update, cq = make_update(CB_AI_TTI)
    result = await on_ai_tti_stale(update, make_context())
    assert result is None
    cq.answer.assert_awaited_once()
    assert cq.answer.await_args.args[0] == copy.TOAST_IMAGE_GEN_UNAVAILABLE
    cq.message.edit_text.assert_not_awaited()
