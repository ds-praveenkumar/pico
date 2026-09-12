"""
Async logging setup using rich
"""

import logging
import os
import threading
from logging.handlers import QueueHandler, QueueListener
from queue import Queue

from rich.logging import RichHandler

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_LEVEL = getattr(logging, _LOG_LEVEL, logging.INFO)

_listener = None
_lock = threading.Lock()


def _formatter():
    return logging.Formatter("%(name)s :: %(message)s")


def setup_rich_logging():
    """Attach an async rich handler to root. Safe to call multiple times."""
    global _listener
    with _lock:
        if _listener is not None:
            return _listener

        handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
            markup=True,
        )
        handler.setFormatter(_formatter())

        queue = Queue(-1)
        queue_handler = QueueHandler(queue)

        listener = QueueListener(queue, handler, respect_handler_level=True)

        root = logging.getLogger()
        root.setLevel(_LEVEL)
        root.addHandler(queue_handler)

        listener.start()
        _listener = listener
        return listener


def stop_rich_logging():
    global _listener
    with _lock:
        if _listener is not None:
            _listener.stop()
            _listener = None


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(_LEVEL)
    logger.propagate = True
    return logger