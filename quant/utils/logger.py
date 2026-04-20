"""Structured logging utilities."""

import logging
import sys
from pathlib import Path
from typing import Optional


_loggers = {}


def setup_logger(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """Setup a logger with console and optional file handler."""
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    logger.handlers.clear()

    if format_string is None:
        format_string = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    formatter = logging.Formatter(format_string)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _loggers[name] = logger
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get an existing logger or create a default one."""
    if name in _loggers:
        return _loggers[name]
    return setup_logger(name)
