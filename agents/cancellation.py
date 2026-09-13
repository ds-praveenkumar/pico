"""Cooperative cancellation primitives for agent runs."""

import threading


class TaskCancelled(Exception):
    """Signal that the master cancelled the current task."""


class CancellationToken:
    """Thread-safe cancellation flag checked between agent turns and tools."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise when cancellation has been requested."""
        if self.is_cancelled:
            raise TaskCancelled("task cancelled")
