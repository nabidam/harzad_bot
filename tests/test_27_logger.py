# encoding: utf-8
import io
import logging
import os
import tempfile
import pytest
from rich.console import Console
from rich.logging import RichHandler

from utils.logger import init_logger, console


def get_app_handlers(root):
    file_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.FileHandler)
        and not h.__class__.__module__.startswith("_pytest")
    ]
    rich_handlers = [
        h
        for h in root.handlers
        if isinstance(h, RichHandler)
        and not h.__class__.__module__.startswith("_pytest")
    ]
    return file_handlers, rich_handlers


def test_init_logger_creates_file_and_rich_handlers(tmp_path):
    logfile = str(tmp_path / "test.log")
    root = init_logger(logfile=logfile)

    file_handlers, rich_handlers = get_app_handlers(root)

    assert len(file_handlers) == 1
    assert len(rich_handlers) == 1
    assert file_handlers[0].level == logging.DEBUG
    assert rich_handlers[0].level == logging.INFO
    assert rich_handlers[0].console.file == console.file


def test_file_captures_all_log_levels(tmp_path):
    logfile = str(tmp_path / "all_levels.log")
    init_logger(logfile=logfile)

    test_logger = logging.getLogger("test.levels")
    test_logger.debug("Debug event for diagnostics")
    test_logger.info("Info event for regular flow")
    test_logger.warning("Warning event for potential issues")
    test_logger.error("Error event for failures")

    # Flush handlers to ensure file content is synced
    for h in logging.getLogger().handlers:
        h.flush()

    with open(logfile, "r", encoding="utf-8") as f:
        content = f.read()

    assert "DEBUG - Debug event for diagnostics" in content
    assert "INFO - Info event for regular flow" in content
    assert "WARNING - Warning event for potential issues" in content
    assert "ERROR - Error event for failures" in content


def test_stdout_rich_handler_respects_console_level(tmp_path, capsys):
    logfile = str(tmp_path / "console.log")
    init_logger(logfile=logfile, console_level=logging.INFO)

    test_logger = logging.getLogger("test.console")
    test_logger.debug("Hidden from console")
    test_logger.info("Visible on console")
    test_logger.warning("Warning on console")

    for h in logging.getLogger().handlers:
        h.flush()

    captured = capsys.readouterr()
    assert "Hidden from console" not in captured.out
    assert "Visible" in captured.out
    assert "console" in captured.out
    assert "Warning" in captured.out
    assert "[test.console]" in captured.out


def test_unicode_and_persian_support_in_file_and_console(tmp_path, capsys):
    logfile = str(tmp_path / "persian.log")
    init_logger(logfile=logfile)

    test_logger = logging.getLogger("test.unicode")
    persian_text = "سلام دنیا - دانلود با موفقیت انجام شد 🎵🔥"
    test_logger.info(persian_text)

    for h in logging.getLogger().handlers:
        h.flush()

    with open(logfile, "r", encoding="utf-8") as f:
        content = f.read()

    assert persian_text in content
    captured = capsys.readouterr()
    assert "سلام دنیا" in captured.out


def test_no_duplicate_handlers_on_reinit(tmp_path):
    logfile = str(tmp_path / "reinit.log")
    init_logger(logfile=logfile)
    init_logger(logfile=logfile)
    init_logger(logfile=logfile)

    root = logging.getLogger()
    file_handlers, rich_handlers = get_app_handlers(root)

    assert len(file_handlers) == 1
    assert len(rich_handlers) == 1


def test_creates_nested_directory_if_missing(tmp_path):
    nested_dir = tmp_path / "nested" / "logs" / "deep"
    logfile = str(nested_dir / "bot.log")

    assert not nested_dir.exists()
    init_logger(logfile=logfile)
    assert nested_dir.exists()

    test_logger = logging.getLogger("test.nested")
    test_logger.info("Created inside nested dir")
    for h in logging.getLogger().handlers:
        h.flush()

    assert os.path.exists(logfile)


def test_init_logger_without_logfile(capsys):
    init_logger(logfile=None)

    root = logging.getLogger()
    file_handlers, rich_handlers = get_app_handlers(root)

    assert len(file_handlers) == 0
    assert len(rich_handlers) == 1

    test_logger = logging.getLogger("test.nofile")
    test_logger.info("Log without file")
    for h in logging.getLogger().handlers:
        h.flush()

    captured = capsys.readouterr()
    assert "Log without" in captured.out
    assert "file" in captured.out
