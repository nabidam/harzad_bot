# encoding: utf-8
import logging


def init_logger(logfile: str):
    """Initialize the root logger and standard log handlers."""
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    # root_logger.setLevel(logging.ERROR)

    # File: every level (incident diagnosis lives in the DEBUG lines —
    # httpcore per-step, ExtBot per-call, spotdl internals).
    file_handler = logging.FileHandler(logfile)
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    # Console: human signal only (INFO and up). The DEBUG flood —
    # one httpcore line pair per socket step, ExtBot per API call —
    # would drown the events that matter while watching the terminal.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
