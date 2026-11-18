# encoding: utf-8
import os
import sys
import logging
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback

# Shared rich console targeting standard output for clean terminal logging
console = Console(stderr=False)


def init_logger(
    logfile: Optional[str] = None,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> logging.Logger:
    """Initialize the root logger with Rich stdout handler and full-level file logging.

    Args:
        logfile: Path to the log file (captures all log levels including DEBUG).
        console_level: Minimum log level for stdout terminal logs (default INFO).
        file_level: Minimum log level for the file handler (default DEBUG).

    Returns:
        The configured root logger instance.
    """
    root_logger = logging.getLogger()
    # The root logger must admit the most verbose level requested so handlers receive it
    root_logger.setLevel(min(file_level, console_level, logging.DEBUG))

    # Remove previously configured application handlers to prevent duplication
    for handler in list(root_logger.handlers):
        if isinstance(
            handler, (logging.FileHandler, RichHandler, logging.StreamHandler)
        ) and not handler.__class__.__module__.startswith("_pytest"):
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    # 1. File: every level (incident diagnosis lives in the DEBUG lines —
    # httpcore per-step, ExtBot per-call, spotdl internals).
    if logfile:
        log_dir = os.path.dirname(os.path.abspath(logfile))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler = logging.FileHandler(logfile, encoding="utf-8")
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(file_level)
        root_logger.addHandler(file_handler)

    # 2. Console: human signal only (INFO and up) rendered professionally via Rich.
    # The DEBUG flood — one httpcore line pair per socket step, ExtBot per API call —
    # would drown the events that matter while watching the terminal.
    # Rich provides color-coded levels, pretty timestamps, module tagging, and
    # formatted tracebacks output specifically to stdout.
    console_formatter = logging.Formatter("[%(name)s] %(message)s")
    console_handler = RichHandler(
        level=console_level,
        console=console,
        show_time=True,
        show_level=True,
        show_path=True,
        enable_link_path=True,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        omit_repeated_times=False,
        log_time_format="[%Y-%m-%d %H:%M:%S]",
        markup=False,
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Pretty tracebacks for uncaught exceptions in terminal
    try:
        install_rich_traceback(console=console, show_locals=False)
    except Exception:
        pass

    return root_logger
