"""#2 utils.helpers — pure text/IO helpers + mocked-telegram helpers."""
import io
import os
from pathlib import Path

import pytest
from PIL import Image
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram import InputFile

from utils import helpers
from configurations.settings import HOST_ROOT, HOSTNAME


# --- text helpers ---------------------------------------------------------

MDV2_SPECIALS = r"\_*[]()~`>#+-=|{}.!"


def test_escape_md_escapes_every_mdv2_special():
    for c in MDV2_SPECIALS:
        assert helpers.escape_md(c) == "\\" + c, c


def test_escape_md_escapes_literal_backslash():
    assert helpers.escape_md("a\\b") == "a\\\\b"


def test_escape_md_plain_text_unchanged():
    assert helpers.escape_md("hello world 123 azAZ") == "hello world 123 azAZ"


def test_escape_md_mixed_sentence():
    assert helpers.escape_md("a*b_c[d](e)~f`g>h#i+j-k=l|m{n}o.p!q") == (
        "a\\*b\\_c\\[d\\]\\(e\\)\\~f\\`g\\>h\\#i\\+j\\-k\\=l\\|m\\{n\\}o\\.p\\!q"
    )


def test_escape_html():
    assert helpers.escape_html("<b>5 < 6</b> & more") == "&lt;b&gt;5 &lt; 6&lt;/b&gt; &amp; more"
    # quote=False: double/single quotes pass through (used for HTML bodies)
    assert helpers.escape_html("a'b\"c") == "a'b\"c"
    assert helpers.escape_html(None) == "None"  # str() coercion


def test_stringify_none_str():
    assert helpers.stringify_none_str(None) == ""
    assert helpers.stringify_none_str("x") == "x"


def test_prepare_link():
    # Current contract: HOST_ROOT -> HOSTNAME prefix swap, then spaces to
    # %20 only. HOSTNAME carries a trailing slash, so the result has a
    # double slash (part of the dead >50MB fallback link, KB open issues).
    src = f"{HOST_ROOT}/videos/My Clip #11.mp4"
    assert helpers.prepare_link(src) == f"{HOSTNAME}/videos/My%20Clip%20#11.mp4"


def test_create_requirement_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    helpers.create_requirement_folders()
    assert (tmp_path / "download").is_dir()
    assert (tmp_path / "login").is_dir()  # LOGIN constant, lowercased


def test_next_proxy_returns_string():
    # PROXY_LIST in .env is the literal "None"; random.choices() over a
    # string yields a single character. Documented current behaviour —
    # see KB open issues (only the IG error-recovery path uses this).
    assert isinstance(helpers.next_proxy(), str)


# --- prepare_cover --------------------------------------------------------

def _make_video_with_thumb(dirpath: Path, ext: str = ".webp", size=(960, 540)):
    import random as _r
    video = dirpath / "clip.mp4"
    video.write_bytes(b"fake mp4 bytes")
    thumb = dirpath / ("clip" + ext)
    img = Image.new("RGB", size)
    pixels = img.load()
    rng = _r.Random(7)  # noise: won't compress tiny at q85
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    img.save(thumb, format="WEBP" if ext == ".webp" else "JPEG", quality=95)
    return video, thumb


def test_prepare_cover_missing_thumb_returns_none(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    assert helpers.prepare_cover(str(video)) is None


def test_prepare_cover_webp_to_jpeg(tmp_path):
    video, thumb = _make_video_with_thumb(tmp_path)
    result = helpers.prepare_cover(str(video))
    assert isinstance(result, InputFile)
    data = result.input_file_content  # PTB InputFile slot holding the bytes
    assert data[:2] == b"\xff\xd8"  # JPEG magic
    assert len(data) <= 190_000
    img = Image.open(io.BytesIO(data))
    assert max(img.size) <= 480


def test_prepare_cover_prefers_jpg_when_both_exist(tmp_path):
    video, _ = _make_video_with_thumb(tmp_path, ext=".webp")
    jpg = tmp_path / "clip.jpg"
    Image.new("RGB", (100, 100), "red").save(jpg, format="JPEG")
    result = helpers.prepare_cover(str(video))
    assert result is not None
    img = Image.open(io.BytesIO(result.input_file_content))
    # The 100x100 red jpg, not the 960x540 noise webp
    assert img.size == (100, 100)


def test_prepare_cover_corrupt_thumb_returns_none(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    (tmp_path / "clip.webp").write_bytes(b"not an image")
    assert helpers.prepare_cover(str(video)) is None


# --- mocked telegram helpers ----------------------------------------------

def _update(msg_id=123, chat_id=99):
    from unittest.mock import MagicMock
    update = MagicMock()
    update.effective_message.message_id = msg_id
    update.effective_message.chat_id = chat_id
    update.effective_chat.id = chat_id
    update.effective_user = None
    return update


async def test_react_message_sets_reaction():
    from unittest.mock import AsyncMock, MagicMock
    update = _update()
    bot = MagicMock()
    bot.set_message_reaction = AsyncMock()
    await helpers.react_message(update, bot, "👍")
    bot.set_message_reaction.assert_awaited_once()
    kwargs = bot.set_message_reaction.await_args.kwargs
    assert kwargs["chat_id"] == 99
    assert kwargs["message_id"] == 123
    assert kwargs["reaction"].emoji == "👍"


async def test_react_message_swallows_telegram_error():
    from unittest.mock import AsyncMock, MagicMock
    update = _update()
    bot = MagicMock()
    bot.set_message_reaction = AsyncMock(side_effect=BadRequest("Reaction_invalid"))
    await helpers.react_message(update, bot, "🚀")  # must not raise


async def test_react_message_no_message_is_noop():
    from unittest.mock import AsyncMock, MagicMock
    update = MagicMock()
    update.effective_message = None
    update.effective_chat = None
    bot = MagicMock()
    bot.set_message_reaction = AsyncMock()
    await helpers.react_message(update, bot, "👍")
    bot.set_message_reaction.assert_not_awaited()


async def test_send_md_msg_uses_markdown_v2():
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.send_message = AsyncMock()
    await helpers.send_md_msg(bot, 99, "a *b*")
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.MARKDOWN_V2
    assert kwargs["text"] == "a \\*b\\*"


async def test_send_html_msg_uses_html():
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.send_message = AsyncMock()
    await helpers.send_html_msg(bot, 99, "<b>x</b>")
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["text"] == "<b>x</b>"


async def test_send_msg_plain():
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.send_message = AsyncMock()
    await helpers.send_msg(bot, 99, "hi")
    assert bot.send_message.await_args.kwargs["chat_id"] == 99


async def test_send_image_protects_content():
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    await helpers.send_image(bot, 99, "photo-obj", caption="c")
    kwargs = bot.send_photo.await_args.kwargs
    assert kwargs["protect_content"] is True
    assert kwargs["parse_mode"] == ParseMode.HTML
