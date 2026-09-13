"""Shared interface for pico user-interface backends."""

from typing import Any, Dict, List, Protocol


class UIBackend(Protocol):
    """Receive live task events from the agent layer."""

    enabled: bool

    def start(self) -> None:
        """Enter the interactive UI lifecycle."""

    def stop(self) -> None:
        """Leave the interactive UI lifecycle."""

    def set_plan(self, steps: List[Dict[str, str]]) -> None:
        """Display a task plan."""

    def mark_step(self, index: int, status: str, note: str = "") -> None:
        """Update one plan step."""

    def set_status(self, text: str) -> None:
        """Update the current status line."""

    def add_log(self, line: str) -> None:
        """Append one captured log line."""

    def set_output(self, agent_name: str, text: str) -> None:
        """Append streamed agent or tool output."""

    def show_reply(self, text: str, meta: str = "") -> None:
        """Display a completed reply."""

    def set_running(self, running: bool) -> None:
        """Mark whether a task is running."""

    def accumulate_usage(self, counts: Dict[str, int]) -> None:
        """Add token usage from one model request."""

    def note_request(self, agent_name: str) -> None:
        """Record one model request."""

    def ask_yes_no(self, question: str) -> bool:
        """Ask a yes-or-no question."""

    def ask_text(self, question: str, max_len: int = 110) -> str:
        """Ask for a free-text answer."""
