"""Headless tests for the Textual TUI (app, navigation, settings, modals).

These tests drive the Textual app through its public API and the
``run_test()`` headless pilot without touching a real terminal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, ListView, Select, Switch  # noqa: E402

from brain.memory import Memory  # noqa: E402
from conftest import FakeLLM  # noqa: E402
from ui.textual_app import (  # noqa: E402
    ApprovalModal,
    CommandPalette,
    HistoryScreen,
    MemoryScreen,
    PicoTUI,
    SettingsScreen,
)
from ui.tui_history import HistoryStore  # noqa: E402

_ZERO_USAGE = {"prompt": 0, "completion": 0, "total": 0}

_TERMINAL = {"ready for your next task", "task cancelled", "task failed"}


async def _wait_for_task(app: PicoTUI, pilot) -> None:
    """Pause until the running task reaches a terminal status."""
    for _ in range(600):
        if app._status in _TERMINAL:
            return
        await pilot.pause(0.025)
    raise AssertionError(f"task did not finish; status={app._status!r} running={app._task_running}")


def _build_tui(tmp_path: Path, *, task: str | None = None, history_enabled: bool = False) -> PicoTUI:
    store = HistoryStore(path=tmp_path)
    store.enabled = history_enabled
    app = PicoTUI(
        llm=FakeLLM(),
        memory=Memory(dir_path=tmp_path / "mem"),
        provider="fake",
        model="unit-test",
        history=store,
        approve=None,
        task=task,
    )
    return app


async def test_tui_boots_and_displays_bindings(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#prompt", Input) is not None
        assert len(app.BINDINGS) > 5


async def test_tui_home_layout_keeps_roomier_spacing(tmp_path):
    """The home view spends extra rows on spacing without collapsing any pane."""
    app = _build_tui(tmp_path)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        assert app.query_one("#statusbar").region.height == 2
        assert app.query_one("#inputbar").region.height == 5
        for widget_id in ("#answer-area", "#plan-pane", "#output", "#log", "#prompt"):
            assert app.query_one(widget_id).content_region.height > 0, widget_id
        assert (
            app.query_one("#usage").content_region.y
            == app.query_one("#running").content_region.y
            == app.query_one("#prompt").content_region.y
        )


async def test_tui_submits_task_and_records_history(tmp_path):
    app = _build_tui(tmp_path, history_enabled=True)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.focus()
        prompt.value = "read the README.md file and tell me what it says"
        await pilot.press("enter")
        await _wait_for_task(app, pilot)
        assert app._status == "ready for your next task"
        records = app.history.load()
        assert len(records) == 1
        assert "report" in records[0]["reply"].lower()


async def test_pending_task_is_auto_submitted(tmp_path):
    app = _build_tui(tmp_path, task="read the README.md file and tell me what it says", history_enabled=True)
    async with app.run_test() as pilot:
        await _wait_for_task(app, pilot)
        assert app.history.load()


async def test_history_navigation(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        app.action_history()
        await pilot.pause()
        assert isinstance(app.screen, HistoryScreen)
        app.screen.action_back()
        await pilot.pause()
        assert not isinstance(app.screen, HistoryScreen)


async def test_memory_navigation(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        app.action_memory()
        await pilot.pause()
        assert isinstance(app.screen, MemoryScreen)


async def test_settings_toggle_history_and_log_level(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        assert app.history.enabled is False
        app.action_settings()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        switch = screen.query_one("#history-toggle", Switch)
        switch.value = True
        await pilot.pause()
        assert app.history.enabled is True
        switch.value = False
        await pilot.pause()
        assert app.history.enabled is False


async def test_settings_applies_terminal_font_size(tmp_path, monkeypatch):
    """Picking a size in Settings stores it and emits the terminal sequence."""
    monkeypatch.delenv("PICO_FONT_SIZE", raising=False)
    applied: list = []
    monkeypatch.setattr("ui.textual_app.apply_font_size", lambda size=None: applied.append(size) or True)
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        assert applied == [None]
        app.action_settings()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        screen.query_one("#font-size", Select).value = "20"
        await pilot.pause()
        assert app.font_size == 20
        assert applied[-1] == 20


async def test_approval_modal_dismiss(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        result_holder: list = []
        async def push_approval() -> None:
            result_holder.append(await app.push_screen_wait(ApprovalModal("allow this?")))
        app.run_worker(push_approval(), name="approval-test")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(50):
            await pilot.pause(0.02)
            if result_holder:
                break
        assert result_holder == [True]


async def test_command_palette_returns_chosen_command(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        result: list = []
        async def push_palette() -> None:
            result.append(await app.push_screen_wait(CommandPalette()))
        app.run_worker(push_palette(), name="palette-test")
        await pilot.pause()
        palette = app.screen
        assert isinstance(palette, CommandPalette)
        palette.query_one("#palette-list", ListView).index = 1
        palette.action_submit()
        for _ in range(50):
            await pilot.pause(0.02)
            if result:
                break
        assert result == ["memory"]


async def test_ask_text_returns_empty_directly_on_event_loop(tmp_path):
    app = _build_tui(tmp_path)
    async with app.run_test() as pilot:
        assert app.ask_text("question?") == ""