"""Textual TUI interface for pico.

A full-screen terminal app with a home view — live answer, task plan, streamed
agent/tool output, log tail, token usage, and a prompt line — plus secondary
screens (History, Memory, Settings, Help) and modal prompts for tool approvals,
free-text questions, multi-line composition, the command palette, and history
export.

The agent layer runs as a Textual worker so the UI stays reactive: LLM calls and
blocking tools run in worker threads, approval callbacks pop async modals, and
the synchronous ``ask_master`` tool is bridged back to the event loop from its
worker thread before blocking.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.markup import escape
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    RichLog,
    Select,
    Static,
    Switch,
    TabPane,
    TabbedContent,
    TextArea,
)

from agents.cancellation import CancellationToken, TaskCancelled
from agents.pico import Pico
from agents.tools import ask as ask_tools
from agents.approval import auto_approve
from brain.logging_setup import capture_logs, current_log_level, drained_logs, get_logger, set_log_level
from ui.fontsize import FONT_SIZES, apply_font_size, font_size_from_env
from ui.tui_history import HistoryStore

logger = get_logger(__name__)

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_frame = 0

OUTPUT_MODES: List[Tuple[str, str]] = [
    ("normal — everything with an agent prefix", "normal"),
    ("compact — only tool results", "compact"),
    ("raw — verbatim stream", "raw"),
]

LOG_LEVELS: List[Tuple[str, str]] = [("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")]

FONT_SIZE_OPTIONS: List[Tuple[str, str]] = [(f"{size} pt", str(size)) for size in FONT_SIZES]


class PicoTUI(App):
    """The Textual user interface for pico (also a :class:`UIBackend`)."""

    TITLE = "pico"
    SUB_TITLE = "your day-to-day assistant"
    enabled: bool = True

    CSS = """
    Screen {
        background: $surface;
    }

    #statusbar {
        dock: top;
        height: 2;
        padding: 0 2;
        color: $text;
        background: $primary-darken-2;
        text-style: bold;
    }

    #answer-area {
        height: 30%;
        border: round $primary;
        margin: 0 2;
        padding: 0 2;
    }

    #meta {
        color: $text-muted;
    }

    #main {
        height: 1fr;
        padding: 1 2;
    }

    #plan-pane {
        width: 2fr;
        border: round $accent;
        padding: 0 2;
    }

    #stream-pane {
        width: 3fr;
    }

    #output {
        height: 62%;
        border: round $panel;
        padding: 0 2;
    }

    #log {
        height: 38%;
        border: round $panel;
        padding: 0 2;
    }

    #inputbar {
        dock: bottom;
        height: 5;
        padding: 1 2;
        background: $background;
    }

    #usage {
        width: auto;
        color: $text-muted;
        padding-top: 1;
    }

    #running {
        width: 14;
        color: $success;
        padding-top: 1;
        text-style: bold;
    }

    #prompt {
        width: 1fr;
    }

    HistoryScreen,
    MemoryScreen,
    SettingsScreen,
    HelpScreen {
        padding: 1 2;
    }

    .screen-title {
        height: 2;
        text-style: bold;
        color: $accent;
        padding: 0 2;
    }

    .back-row {
        height: 5;
        padding: 1 2;
    }

    .modal-box {
        width: 76;
        max-height: 24;
        border: round $primary;
        background: $surface;
        padding: 2 3;
    }

    .hint {
        color: $text-muted;
    }

    #compose-area {
        height: 10;
        border: round $accent;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel task", priority=True),
        Binding("q", "quit", "Quit"),
        Binding("h", "history", "History"),
        Binding("m", "memory", "Memory"),
        Binding("s", "settings", "Settings"),
        Binding("?", "help", "Help"),
        Binding("ctrl+k", "palette", "Commands"),
        Binding("ctrl+n", "compose", "Compose"),
        Binding("ctrl+e", "export_history", "Export history"),
        Binding("ctrl+l", "clear_output", "Clear output"),
    ]

    def __init__(
        self,
        llm: Any,
        memory: Any,
        provider: str = "",
        model: str = "",
        history: Optional[HistoryStore] = None,
        approve: Optional[Any] = None,
        task: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.llm = llm
        self.memory = memory
        self.provider = provider
        self.model = model
        self.history = history or HistoryStore()
        self.custom_approve = approve
        self._pending_task = (task or "").strip() or None
        self.pico: Optional[Pico] = None
        self.cancel_token = CancellationToken()
        self.enabled = True
        self.output_mode = "normal"
        self.show_tool_output = True
        self.font_size: Optional[int] = font_size_from_env()
        self._task_running = False
        self._on_main = True
        self._status = "ready — describe a task below"
        self._plan: List[Dict[str, str]] = []
        self._usage: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self._requests = 0
        self._task_events: List[Dict[str, Any]] = []
        self._active_task = ""
        self._input_history: List[str] = []
        self._history_index: Optional[int] = None

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """UIBackend lifecycle hook; Textual manages its own run loop."""

    def stop(self) -> None:
        """UIBackend lifecycle hook; Textual manages its own run loop."""

    def on_mount(self) -> None:
        """Build the pico pipeline and begin capturing logs."""
        apply_font_size(self.font_size)
        self.pico = Pico(llm=self.llm, approve=self._approve_async, memory=self.memory)
        self.pico.on_plan = self.set_plan
        self.pico.on_step = self.mark_step
        self.pico.on_generate = self._on_generate
        self.pico.on_tool = self._on_tool
        self._refresh_cancel_token()
        ask_tools.set_master_prompt(self.ask_text)
        capture_logs(True)

        self.query_one("#plan", DataTable).add_columns("Status", "Step")
        self.query_one("#usage", Static).update(self._usage_line())
        self.query_one("#statusbar", Static).update(self._status)
        self.set_interval(0.15, self._tick_spinner)
        self.set_interval(0.4, self._drain_logs)
        if self._pending_task is not None:
            task = self._pending_task
            self._pending_task = None
            self.call_after_refresh(lambda: self._submit_task(task))
        self.focus_prompt()

    def on_unmount(self) -> None:
        """Restore normal console logging and clear the master-prompt bridge."""
        capture_logs(False)
        ask_tools.set_master_prompt(None)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="statusbar")
        with VerticalScroll(id="answer-area"):
            yield Markdown("", id="answer")
            yield Static("", id="meta")
        with Horizontal(id="main"):
            with Vertical(id="plan-pane"):
                yield DataTable(id="plan", zebra_stripes=True, cursor_type="row")
            with Vertical(id="stream-pane"):
                yield RichLog(id="output", markup=False, wrap=True, max_lines=1000)
                yield RichLog(id="log", markup=True, wrap=True, max_lines=500)
        with Horizontal(id="inputbar"):
            yield Static("", id="usage")
            yield Static("", id="running")
            yield Input(
                placeholder="Describe a task for pico…   (ctrl+k commands · h history · ? help)",
                id="prompt",
            )
        yield Footer()

    def focus_prompt(self) -> None:
        """Focus the task input line when the home screen is visible."""
        if self._on_main:
            try:
                self.query_one("#prompt", Input).focus()
            except Exception:  # noqa: BLE001 - focus during mount is not fatal
                pass

    def _usage_line(self) -> str:
        u = self._usage
        return (
            f"tokens {u['prompt']} prompt · {u['completion']} completion · {u['total']} total"
            f" · {self._requests} request(s)"
        )

    def _tick_spinner(self) -> None:
        global _frame
        if self._task_running:
            self.query_one("#running", Static).update(_SPINNER[_frame % len(_SPINNER)] + " working")
            _frame += 1

    def _drain_logs(self) -> None:
        for line in drained_logs():
            try:
                self.query_one("#log", RichLog).write(line)
            except Exception:  # noqa: BLE001 - log draining must never crash the UI
                pass

    # ---- UIBackend events --------------------------------------------------

    def set_plan(self, steps: List[Dict[str, str]]) -> None:
        """Display the parsed task plan with all steps pending."""
        self._plan = [
            {
                "agent": str(step.get("agent", "executor")),
                "task": str(step.get("task", "")),
                "status": "pending",
            }
            for step in steps
        ]
        self._render_plan()

    def mark_step(self, index: int, status: str, note: str = "") -> None:
        """Update one plan step's run state."""
        if 0 <= index < len(self._plan):
            self._plan[index]["status"] = status
        self._render_plan()

    def _render_plan(self) -> None:
        table = self.query_one("#plan", DataTable)
        table.clear()
        for index, step in enumerate(self._plan):
            table.add_row(str(step.get("status", "pending")), f"{step['agent']}: {step['task']}", key=index)

    def set_status(self, text: str) -> None:
        """Update the status bar line."""
        self._status = text
        self.query_one("#statusbar", Static).update(text)

    def add_log(self, line: str) -> None:
        """Append one log-style line to the capture pane."""
        self.query_one("#log", RichLog).write(line)

    def set_output(self, agent_name: str, text: str) -> None:
        """Stream agent output / tool results into the live pane and history."""
        if not text:
            return
        self._task_events.append({"agent": agent_name, "text": text, "ts": _now_iso()})
        if self.output_mode == "raw":
            body = text
        elif self.output_mode == "compact":
            if not text.startswith("["):
                return
            body = f"{agent_name} ▶ {text}"
        else:
            body = f"{agent_name} › {text}"
        self.query_one("#output", RichLog).write(body)

    def show_reply(self, text: str, meta: str = "") -> None:
        """Display pico's completed answer and metadata."""
        self.query_one("#answer", Markdown).update(text or "(empty reply)")
        self.query_one("#meta", Static).update(meta or "")

    def set_running(self, running: bool) -> None:
        """Mark whether a task is currently running."""
        self._task_running = running
        if running:
            self.query_one("#running", Static).update("● working")
        else:
            self.query_one("#running", Static).update("")
            self.focus_prompt()

    def accumulate_usage(self, counts: Dict[str, int]) -> None:
        """Add token usage from one model request."""
        for key in self._usage:
            self._usage[key] += int(counts.get(key, 0) or 0)
        self.query_one("#usage", Static).update(self._usage_line())

    def note_request(self, agent_name: str) -> None:
        """Record one LLM request."""
        self._requests += 1
        self.query_one("#usage", Static).update(self._usage_line())

    async def ask_yes_no(self, question: str) -> bool:
        """Ask a yes/no approval question inside a modal screen."""
        return bool(await self.push_screen_wait(ApprovalModal(question)))

    def ask_text(self, question: str, max_len: int = 110) -> str:
        """Blocking free-text question, bridged from the agent's worker thread."""
        try:
            asyncio.get_running_loop()
            return ""  # never block the UI loop; ask_master always runs off-thread
        except RuntimeError:
            holder: Dict[str, Any] = {"event": threading.Event(), "answer": ""}
            self.call_from_thread(self._ask_modal_from_thread, question, max_len, holder)
            holder["event"].wait(timeout=1800)
            return holder["answer"]

    def _ask_modal_from_thread(self, question: str, max_len: int, holder: Dict[str, Any]) -> None:
        """Schedule the ask modal on the event loop and bridge its result back."""

        async def flow() -> None:
            answer = await self.push_screen_wait(AskModal(question, max_len=max_len))
            holder["answer"] = (answer or "").strip()
            holder["event"].set()

        self.run_worker(flow(), name="ask-bridge", group="ask")

    async def _approve_async(self, name: str, args: Dict[str, Any]) -> bool:
        """Shared approval policy: auto-approve safe calls, modal for the rest."""
        if auto_approve(name, args):
            self.add_log(f"auto-approved [{name}]")
            return True
        if self.custom_approve is not None:
            return bool(await self.custom_approve(name, args))
        question = f"pico wants to call [b]{name}[/b]({args})"
        return bool(await self.push_screen_wait(ApprovalModal(question)))

    def _on_generate(self, agent_name: str, content: str = "") -> None:
        """Hook: one LLM generation finished."""
        self.note_request(agent_name)
        if self.llm is not None:
            self.accumulate_usage(getattr(self.llm, "last_generation", self._usage))
        if content and self.show_tool_output:
            self.set_output(agent_name, content)

    def _on_tool(self, agent_name: str, content: str = "") -> None:
        """Hook: one tool call produced streaming output."""
        if content:
            self.set_output(agent_name, content)

    def _refresh_cancel_token(self) -> None:
        """Push the active cancellation token into pico and both sub-agents."""
        if self.pico is None:
            return
        self.pico.cancel_token = self.cancel_token
        self.pico.executor.cancel_token = self.cancel_token
        self.pico.researcher.cancel_token = self.cancel_token

    # ---- task input --------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle the prompt line: built-in commands or a new task."""
        task = event.value.strip()
        if not task:
            return
        self._push_input_history(task)
        event.input.value = ""
        if self._handle_command(task):
            return
        self._submit_task(task)

    def _handle_command(self, task: str) -> bool:
        """Route built-in prompt commands; return True when consumed."""
        command = task.lower()
        routing = {
            "exit": "quit",
            "quit": "quit",
            "q": "quit",
            "clear": "clear_output",
            "cls": "clear_output",
            "help": "help",
            "?": "help",
            "history": "history",
            "hist": "history",
            "memory": "memory",
            "mem": "memory",
            "settings": "settings",
            "prefs": "settings",
            "compose": "compose",
            "export": "export_history",
            "commands": "palette",
        }
        name = routing.get(command)
        if name is None:
            return False
        action = getattr(self, f"action_{name}", None)
        if action is None:
            return False
        result = action()
        if inspect.isawaitable(result):
            self.run_worker(result, name=f"action-{name}", group="actions")
        return True

    def _push_input_history(self, task: str) -> None:
        if self._input_history and self._input_history[-1] == task:
            return
        self._input_history.append(task)
        del self._input_history[:-100]
        self._history_index = None

    def on_key(self, event: events.Key) -> None:
        """Provide up/down history browsing while the prompt is focused."""
        if not self._on_main:
            return
        prompt = self.query_one("#prompt", Input)
        if self.focused is not prompt:
            return
        if event.key == "up":
            event.stop()
            self._step_input_history(1)
        elif event.key == "down":
            event.stop()
            self._step_input_history(-1)

    def _step_input_history(self, direction: int) -> None:
        prompt = self.query_one("#prompt", Input)
        if not self._input_history:
            return
        if direction > 0:  # newer
            if self._history_index is None:
                return
            if self._history_index < len(self._input_history) - 1:
                self._history_index += 1
            else:
                self._history_index = None
                prompt.value = ""
                return
        else:  # older
            if self._history_index is None:
                self._history_index = len(self._input_history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
        prompt.value = self._input_history[self._history_index]
        prompt.cursor_position = len(prompt.value)

    def _submit_task(self, task: str) -> None:
        """Kick off one task through the async pico pipeline."""
        if self._task_running:
            self.notify("pico is busy — press ctrl+c to cancel the current task", severity="warning", title="busy")
            return
        self._active_task = task
        self._task_events = []
        self._plan = []
        self.cancel_token = CancellationToken()
        self._refresh_cancel_token()
        self.query_one("#answer", Markdown).update("")
        self.query_one("#meta", Static).update("")
        self.query_one("#output", RichLog).clear()
        self.set_plan([])
        self.set_status("working on your task…")
        self.set_running(True)
        self.run_worker(self._run_task_async(task), name="task", group="task", exclusive=True, exit_on_error=False)

    async def _run_task_async(self, task: str) -> None:
        """Execute one task; record history only on success."""
        try:
            reply = await self.pico.run_async(task)
            reply = (reply or "").strip() or "(no reply — check the log for details)"
            self.show_reply(reply, f"{task[:60]} · {self._usage_line()}")
            if self.history.enabled:
                self.history.record(task, reply, self._plan, self._usage, self._task_events)
            self.set_status("ready for your next task")
        except TaskCancelled:
            self.show_reply("*Task cancelled by the master.*", "cancelled")
            self.set_status("task cancelled")
        except Exception as exc:  # noqa: BLE001 - a task failure must not kill the app
            logger.error("[bold red]Task failed[/bold red]: %s", exc)
            self.show_reply(f"*Task failed:* {exc}", "task failed")
            self.set_status("task failed")
            self.notify(f"Task failed: {exc}", severity="error", title="pico")
        finally:
            self.set_running(False)
            self.focus_prompt()

    # ---- key bindings / navigation ----------------------------------------

    def action_quit(self) -> None:
        self.exit()

    def action_interrupt(self) -> None:
        """Cancel the running task (cooperative via CancellationToken)."""
        if self._task_running and self.cancel_token is not None and not self.cancel_token.is_cancelled:
            self.cancel_token.cancel()
            self.set_status("cancelling…")

    def action_clear_output(self) -> None:
        self.query_one("#output", RichLog).clear()
        self.set_status("cleared output")

    def action_history(self) -> None:
        self._push(HistoryScreen(self.history))

    def action_memory(self) -> None:
        self._push(MemoryScreen(self.memory))

    def action_settings(self) -> None:
        self._push(SettingsScreen(self))

    def action_help(self) -> None:
        self._push(HelpScreen())

    async def action_palette(self) -> None:
        self._on_main = False
        command = await self.push_screen_wait(CommandPalette())
        self._on_main = True
        self.focus_prompt()
        self._dispatch_palette(command)

    async def action_compose(self) -> None:
        self._on_main = False
        text = await self.push_screen_wait(ComposeModal())
        self._on_main = True
        self.focus_prompt()
        if text and text.strip():
            task = text.strip()
            self._push_input_history(task)
            if not self._handle_command(task):
                self._submit_task(task)

    async def action_export_history(self) -> None:
        self._on_main = False
        target = await self.push_screen_wait(ExportHistoryModal(self.history))
        self._on_main = True
        self.focus_prompt()
        if target is not None:
            self.notify(f"History exported to {target}", title="history")

    def _push(self, screen: Screen) -> None:
        self._on_main = False
        self.push_screen(screen)

    def action_back(self) -> None:
        self.pop_screen()
        self._on_main = True
        self.focus_prompt()

    def _dispatch_palette(self, command: Optional[str]) -> None:
        if command is None:
            return
        handlers: Dict[str, Any] = {
            "history": self.action_history,
            "memory": self.action_memory,
            "settings": self.action_settings,
            "help": self.action_help,
            "clear": self.action_clear_output,
            "compose": self.action_compose,
            "export": self.action_export_history,
            "quit": self.action_quit,
        }
        handler = handlers.get(command)
        if handler is None:
            return
        result = handler()
        if inspect.isawaitable(result):
            self.run_worker(result, name=f"palette-{command}", group="actions")


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with a Z suffix, seconds precision."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class BaseScreen(Screen):
    """Shared styling and back-navigation for secondary screens."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]

    def action_back(self) -> None:
        self.app.action_back()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.action_back()


class HistoryScreen(BaseScreen):
    """Browse redacted past tasks, most recent first."""

    def __init__(self, store: HistoryStore) -> None:
        super().__init__()
        self.store = store
        self.records: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Task history — redacted records from this machine", classes="screen-title", markup=True)
        yield DataTable(id="history-table", zebra_stripes=True, cursor_type="row")
        yield Horizontal(Button("Back", id="back-btn", variant="default"), classes="back-row")

    def on_mount(self) -> None:
        self.records = self.store.load()
        table = self.query_one("#history-table", DataTable)
        table.add_columns("When", "Task", "Status")
        for index, record in enumerate(self.records):
            when = str(record.get("ts", ""))[:19]
            table.add_row(when, str(record.get("task", "")).replace("\n", " ")[:80], str(record.get("status", "")), key=index)

    @on(DataTable.RowSelected, "#history-table")
    def _show_detail(self, event: DataTable.RowSelected) -> None:
        index = int(event.row_key.value)
        record = self.records[index]
        self.app.push_screen(HistoryDetailScreen(record))


class HistoryDetailScreen(ModalScreen[None]):
    """Show one redacted history record in full."""

    CSS = """
    Screen { align: center middle; }
    #detail-box { width: 90; height: 80%; border: round $accent; background: $surface; padding: 1 2; }
    #detail-scroll { height: 1fr; }
    """

    def __init__(self, record: Dict[str, Any]) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-box"):
            yield Static("History record", classes="screen-title", markup=True)
            with VerticalScroll(id="detail-scroll"):
                yield Static(self._render(), markup=True, id="detail-body")
            yield Horizontal(Button("Close", id="back-btn", variant="default"), classes="back-row")

    def _render(self) -> str:
        record = self.record
        lines = [
            f"[b]When:[/b] {escape(str(record.get('ts', '')))}",
            f"[b]Status:[/b] {escape(str(record.get('status', '')))}",
            f"[b]Task:[/b]\n{escape(str(record.get('task', '')))}",
            "",
            f"[b]Reply:[/b]\n{escape(str(record.get('reply', '')))}",
            "",
            "[b]Plan:[/b]",
        ]
        for step in record.get("plan", []) or []:
            lines.append(
                f"- [{escape(str(step.get('status', '')))}] {escape(str(step.get('agent', '')))}: "
                f"{escape(str(step.get('task', '')))}"
            )
        usage = record.get("usage", {}) or {}
        lines.append("")
        lines.append(
            f"[b]Tokens:[/b] {escape(str(usage.get('prompt', 0)))} prompt · {escape(str(usage.get('completion', 0)))}"
            f" completion · {escape(str(usage.get('total', 0)))} total"
        )
        events = record.get("events", []) or []
        if events:
            lines.append("")
            lines.append(f"[b]Events ({len(events)}):[/b]")
            for event in events[-20:]:
                stamp = str(event.get("ts", ""))[11:19]
                lines.append(f"- ({escape(stamp)}) [{escape(str(event.get('agent', '')))}] {escape(str(event.get('text', '')))[:160]}")
        return "\n".join(lines)

    def action_back(self) -> None:
        self.dismiss(None)


class MemoryScreen(BaseScreen):
    """Read-only browser over all four memory layers."""

    def __init__(self, memory: Any) -> None:
        super().__init__()
        self.memory = memory

    def compose(self) -> ComposeResult:
        yield Static("Memory — what pico remembers", classes="screen-title", markup=True)
        with TabbedContent():
            with TabPane("Working", id="tab-working"):
                yield VerticalScroll(Static("", id="mem-working", markup=True))
            with TabPane("Episodic", id="tab-episodic"):
                yield VerticalScroll(Static("", id="mem-episodic", markup=True))
            with TabPane("Long-term", id="tab-longterm"):
                yield VerticalScroll(Static("", id="mem-longterm", markup=True))
            with TabPane("Semantic", id="tab-semantic"):
                yield VerticalScroll(Static("", id="mem-semantic", markup=True))
        yield Horizontal(Button("Back", id="back-btn", variant="default"), classes="back-row")

    def on_mount(self) -> None:
        self.refresh_memory()

    def refresh_memory(self) -> None:
        memory = self.memory
        if memory is None:
            for widget_id in ("mem-working", "mem-episodic", "mem-longterm", "mem-semantic"):
                self.query_one(f"#{widget_id}", Static).update("*memory not wired*")
            return
        working = memory.working.snapshot() if hasattr(memory, "working") else {}
        working_text = "\n".join(f"[b]{escape(key)}[/b]: {escape(value)}" for key, value in sorted(working.items())) or "*(empty)*"
        self.query_one("#mem-working", Static).update(working_text)

        episodes = memory.episodes(limit=50) if hasattr(memory, "episodes") else []
        episode_lines = [
            f"[b cyan]({escape(str(e.get('ts', '')))})[/b cyan] {escape(str(e.get('title', '')))}\n{escape(str(e.get('summary', '')))}"
            for e in episodes
        ]
        self.query_one("#mem-episodic", Static).update("\n\n".join(episode_lines) or "*(no episodes yet)*")

        notes = memory.notes() if hasattr(memory, "notes") else {}
        note_lines = [f"[b]{escape(key)}[/b]: {escape(value)}" for key, value in sorted(notes.items())]
        self.query_one("#mem-longterm", Static).update("\n".join(note_lines) or "*(no long-term notes yet)*")

        semantic_items = memory.semantic_items() if hasattr(memory, "semantic_items") else []
        semantic_lines = []
        for item in semantic_items[-50:]:
            text = str(item.get("text", ""))
            meta = item.get("metadata") or {}
            extra = f"  [dim](source: {escape(str(meta.get('source', '?')))})[/dim]" if isinstance(meta, dict) else ""
            semantic_lines.append(f"- {escape(text)}{extra}")
        self.query_one("#mem-semantic", Static).update("\n".join(semantic_lines) or "*(no semantic memories yet)*")


class SettingsScreen(BaseScreen):
    """Session-only settings: log level, output mode, and history capture."""

    def __init__(self, app: PicoTUI) -> None:
        super().__init__()
        self.tui = app

    def compose(self) -> ComposeResult:
        yield Static("Settings — session-only, reset on exit", classes="screen-title", markup=True)
        with VerticalScroll():
            yield Label("Log level")
            yield Select(LOG_LEVELS, value=current_log_level(), id="log-level", allow_blank=False)
            yield Label("Output mode (home stream)")
            yield Select(OUTPUT_MODES, value=self.tui.output_mode, id="output-mode", allow_blank=False)
            yield Label("Terminal font size")
            yield Select(
                FONT_SIZE_OPTIONS,
                value=str(self.tui.font_size) if self.tui.font_size in FONT_SIZES else Select.NULL,
                prompt="terminal default",
                id="font-size",
                allow_blank=True,
            )
            yield Static(
                "Font size is applied with the terminal's font-size sequence (OSC 7770): honoured "
                "by mintty, Ghostty, kitty and Warp, ignored elsewhere — set the size in the "
                "terminal's own preferences there.",
                classes="hint",
                markup=True,
            )
            yield Label("Show LLM/tool stream in the output pane")
            yield Switch(value=self.tui.show_tool_output, id="stream-toggle")
            yield Label("Record task history (redacted, JSONL on disk)")
            yield Switch(value=self.tui.history.enabled, id="history-toggle")
            yield Static(
                "History is opt-in: when off, nothing is written. Approvals are always confirmed on screen.",
                classes="hint",
                markup=True,
            )
        yield Horizontal(Button("Back", id="back-btn", variant="default"), classes="back-row")

    @on(Select.Changed, "#log-level")
    def _change_log_level(self, event: Select.Changed) -> None:
        set_log_level(str(event.value))
        self.tui.notify(f"Log level -> {event.value}", title="settings")

    @on(Select.Changed, "#output-mode")
    def _change_output_mode(self, event: Select.Changed) -> None:
        self.tui.output_mode = str(event.value)

    @on(Select.Changed, "#font-size")
    def _change_font_size(self, event: Select.Changed) -> None:
        if event.value == Select.NULL:
            return
        size = int(str(event.value))
        self.tui.font_size = size
        apply_font_size(size)
        self.tui.notify(f"Terminal font size -> {size} pt", title="settings")

    @on(Switch.Changed, "#stream-toggle")
    def _change_stream(self, event: Switch.Changed) -> None:
        self.tui.show_tool_output = event.value

    @on(Switch.Changed, "#history-toggle")
    def _change_history(self, event: Switch.Changed) -> None:
        self.tui.history.enabled = event.value
        state = "enabled" if event.value else "disabled"
        self.tui.notify(f"Task history {state}", title="settings")


class HelpScreen(BaseScreen):
    """Reference for pico's TUI bindings and features."""

    def compose(self) -> ComposeResult:
        yield Static("Help", classes="screen-title", markup=True)
        with VerticalScroll():
            yield Static(
                "\n".join(
                    [
                        "[b]pico — day-to-day assistant[/b]",
                        "",
                        "Type a task at the prompt and press Enter. pico plans it, delegates to",
                        "sub-agents (executor / researcher), and summarizes the result here.",
                        "",
                        "[b]Key bindings[/b]",
                        "  ctrl+c   cancel the running task",
                        "  ctrl+k   command palette",
                        "  ctrl+n   compose a multi-line task",
                        "  ctrl+e   export redacted task history",
                        "  ctrl+l   clear the output pane",
                        "  h        browse task history",
                        "  m        review memory",
                        "  s        session settings (log level, output mode, history)",
                        "  ?        this help",
                        "  q        quit",
                        "  up/down  browse previous prompts (at the prompt line)",
                        "",
                        "[b]Safety[/b]",
                        "Risky tool calls (shell commands, gmail sends, calendar writes, sheet",
                        "appends) pause for your approval on screen. ask_master questions",
                        "(CAPTCHA, OTP, personal details) open a modal too.",
                        "",
                        "[b]History[/b]",
                        "Completed tasks are recorded redacted to JSONL only when history capture",
                        "is enabled in Settings. Emails, browsing, voice, calendar, and sheet",
                        "payloads are always scrubbed from the record.",
                    ]
                ),
                markup=True,
            )
        yield Horizontal(Button("Back", id="back-btn", variant="default"), classes="back-row")


class ApprovalModal(ModalScreen[bool]):
    """Yes/no approval prompt for a higher-risk tool call."""

    CSS = """
    Screen { align: center middle; }
    #approval-box { border: round $error; }
    #approval-question { padding: 0 0 1 0; }
    #approval-actions { height: 3; }
    """

    BINDINGS = [Binding("y", "approve", "Yes"), Binding("n", "reject", "No")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box", classes="modal-box"):
            yield Static("Approval required", classes="screen-title", markup=True)
            yield Static(self.question, markup=True, id="approval-question")
            yield Horizontal(
                Button("Reject (n)", variant="error", id="approve-no"),
                Button("Approve (y)", variant="success", id="approve-yes"),
                id="approval-actions",
            )

    def on_mount(self) -> None:
        self.query_one("#approve-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve-yes")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_reject(self) -> None:
        self.dismiss(False)


class AskModal(ModalScreen[str]):
    """Free-text answer modal for the ask_master tool."""

    CSS = """
    Screen { align: center middle; }
    #ask-box { border: round $primary; }
    #ask-question { padding: 0 0 1 0; }
    """

    def __init__(self, question: str, max_len: int = 110) -> None:
        super().__init__()
        self.question = question
        self.max_len = max_len

    def compose(self) -> ComposeResult:
        with Vertical(id="ask-box", classes="modal-box"):
            yield Static("pico needs the master", classes="screen-title", markup=True)
            yield Static(self.question, markup=True, id="ask-question")
            yield Input(placeholder="Your answer… (enter to submit)", id="ask-input", max_length=self.max_len)
            yield Static("esc cancels the answer", classes="hint", markup=True)

    def on_mount(self) -> None:
        self.query_one("#ask-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class ComposeModal(ModalScreen[str]):
    """Multi-line task composition."""

    CSS = """
    Screen { align: center middle; }
    #compose-box { border: round $accent; width: 90; }
    """

    BINDINGS = [Binding("ctrl+n", "submit", "Send task")]

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box", classes="modal-box"):
            yield Static("Compose a task", classes="screen-title", markup=True)
            yield TextArea("", id="compose-area", language="markdown")
            yield Static("ctrl+n or Send to submit · esc to cancel", classes="hint", markup=True)
            yield Horizontal(
                Button("Cancel", variant="default", id="compose-cancel"),
                Button("Send", variant="success", id="compose-send"),
                id="approval-actions",
            )

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compose-send":
            self.action_submit()
        elif event.button.id == "compose-cancel":
            self.dismiss("")

    def action_submit(self) -> None:
        self.dismiss(self.query_one(TextArea).text)


class CommandPalette(ModalScreen[Optional[str]]):
    """Filterable command palette launched from ctrl+k."""

    CSS = """
    Screen { align: center middle; }
    #palette-box { border: round $primary; width: 80; }
    #palette-list { height: 12; }
    """

    BINDINGS = [Binding("enter", "submit", "Run")]

    COMMANDS: List[Tuple[str, str]] = [
        ("history", "History — browse redacted past tasks"),
        ("memory", "Memory — review what pico remembers"),
        ("settings", "Settings — log level, output, history capture"),
        ("help", "Help — keybindings and features"),
        ("clear", "Clear output pane"),
        ("compose", "Compose — multi-line task input"),
        ("export", "Export history — redacted JSONL copy"),
        ("quit", "Quit pico"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._items: List[Tuple[str, str]] = list(self.COMMANDS)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-box", classes="modal-box"):
            yield Static("Commands", classes="screen-title", markup=True)
            yield Input(placeholder="Filter commands…", id="palette-input")
            yield ListView(id="palette-list")

    def on_mount(self) -> None:
        self._render_items()
        self.query_one("#palette-input", Input).focus()

    def _render_items(self) -> None:
        list_view = self.query_one("#palette-list", ListView)
        list_view.clear()
        list_view.extend(
            ListItem(Static(f"[b]{name}[/b]  {description}", markup=True)) for name, description in self._items
        )
        if self._items:
            list_view.index = 0

    @on(Input.Changed, "#palette-input")
    def _filter(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            self._items = list(self.COMMANDS)
        else:
            self._items = [(name, desc) for name, desc in self.COMMANDS if query in name or query in desc.lower()]
        self._render_items()

    def action_submit(self) -> None:
        list_view = self.query_one("#palette-list", ListView)
        if list_view.index is None or not self._items:
            self.dismiss(None)
            return
        self.dismiss(self._items[list_view.index][0])

    @on(ListView.Selected, "#palette-list")
    def _selected(self, event: ListView.Selected) -> None:
        self.action_submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_submit()


class ExportHistoryModal(ModalScreen[Optional[Path]]):
    """Export the redacted JSONL history to a master-chosen path."""

    CSS = """
    Screen { align: center middle; }
    #export-box { border: round $primary; }
    """

    def __init__(self, store: HistoryStore) -> None:
        super().__init__()
        self.store = store

    def _default_path(self) -> Path:
        base = os.getenv("PICO_MEMORY_PATH", "").strip()
        root = Path(base).expanduser() if base else Path.home() / ".pico"
        return root / "tui_history_export.jsonl"

    def compose(self) -> ComposeResult:
        with Vertical(id="export-box", classes="modal-box"):
            yield Static("Export task history", classes="screen-title", markup=True)
            yield Label("Target path (may be outside the project):")
            yield Input(str(self._default_path()), id="export-path")
            yield Static("The output holds only redacted records.", classes="hint", markup=True)
            yield Horizontal(
                Button("Cancel", variant="default", id="export-cancel"),
                Button("Export", variant="success", id="export-confirm"),
                id="approval-actions",
            )

    def on_mount(self) -> None:
        self.query_one("#export-path", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export-cancel":
            self.dismiss(None)
            return
        if event.button.id != "export-confirm":
            return
        raw = self.query_one("#export-path", Input).value.strip()
        if not raw:
            self.app.notify("Give a target path first", severity="warning", title="export")
            return
        target = Path(raw).expanduser()
        try:
            count = self.store.export(target)
        except OSError as exc:
            self.app.notify(f"Export failed: {exc}", severity="error", title="export")
            return
        self.app.notify(f"Exported {count} record(s) to {target}", title="export")
        self.dismiss(target)