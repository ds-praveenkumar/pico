"""User-interface layer: rich dashboard, Textual TUI, history, backend protocol."""

from ui.dashboard import Dashboard
from ui.tui_history import HistoryStore
from ui.ui_backend import UIBackend

__all__ = ["Dashboard", "HistoryStore", "UIBackend"]