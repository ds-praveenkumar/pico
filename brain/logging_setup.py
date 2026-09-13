"""
Async logging setup using rich
"""

import logging
import os
import threading
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from typing import List

from rich.logging import RichHandler

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_LEVEL = getattr(logging, _LOG_LEVEL, logging.INFO)

_listener = None
_lock = threading.Lock()
_capture: "_RecordBuffer" = None


def _formatter():
    return logging.Formatter("%(name)s :: %(message)s")


def _make_handler() -> RichHandler:
    return RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
    )


class _RecordBuffer(logging.Handler):
    """Collects formatted log lines so a full-screen TUI can display them."""

    def __init__(self) -> None:
        super().__init__()
        self._records: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._records.append(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never crash the listener thread
            pass

    def drain(self) -> List[str]:
        """Return and clear all captured lines."""
        lines = list(self._records)
        self._records.clear()
        return lines


def setup_rich_logging():
    """Attach an async rich handler to root. Safe to call multiple times."""
    global _listener
    with _lock:
        if _listener is not None:
            return _listener

        handler = _make_handler()
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


def capture_logs(enabled: bool) -> None:
    """Swap the async rich handler for an in-memory buffer (TUI mode)."""
    global _listener, _capture
    with _lock:
        if _listener is None:
            return
        if enabled and _capture is None:
            buffer = _RecordBuffer()
            buffer.setFormatter(_formatter())
            _listener.handlers = (buffer,)
            _capture = buffer
        elif not enabled and _capture is not None:
            handler = _make_handler()
            handler.setFormatter(_formatter())
            _listener.handlers = (handler,)
            _capture = None


def drained_logs() -> List[str]:
    """Return and clear the lines captured since the last drain (TUI mode)."""
    with _lock:
        if _capture is None:
            return []
        return _capture.drain()


def current_log_level() -> str:
    """Return the name of the current effective logging level."""
    return logging.getLevelName(_LEVEL)


def set_log_level(level: str) -> None:
    """Change the effective logging level for the whole process at runtime."""
    global _LEVEL, _LOG_LEVEL
    resolved = getattr(logging, str(level).upper(), logging.INFO)
    if not isinstance(resolved, int):
        resolved = logging.INFO
    _LEVEL = resolved
    _LOG_LEVEL = logging.getLevelName(resolved)
    root = logging.getLogger()
    root.setLevel(resolved)
    for name in list(root.manager.loggerDict):
        logging.getLogger(name).setLevel(resolved)


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