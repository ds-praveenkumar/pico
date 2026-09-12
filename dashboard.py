"""Full-screen rich TUI for the pico CLI.

``Dashboard`` renders a Live layout showing the current task plan, live token
usage, per-agent activity, memory sizes, and a tail of captured log lines while
a task runs. It takes over the alternate screen, so the conversation panels that
surround it remain untouched.
"""

import threading
from typing import Any, Dict, List, Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_PENDING = "[cyan]●[/cyan] pending"
_RUNNING = "[yellow]▶[/yellow] running"
_DONE = "[green]✓[/green] done"
_FAILED = "[red]✗[/red] failed"

_STATUS_BY_NAME = {"ple": _PENDING, "running": _RUNNING, "done": _DONE, "failed": _FAILED}


class Dashboard:
    """A live rich layout that tracks one running task."""

    def __init__(
        self,
        console: Console,
        provider: str = "",
        model: str = "",
        memory: Optional[Any] = None,
    ) -> None:
        self.console = console
        self.provider = provider
        self.model = model
        self.memory = memory
        self._plan: List[Dict[str, Any]] = []
        self._status = "thinking…"
        self._log: List[str] = []
        self._usage: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self._requests: Dict[str, int] = {}
        self._live: Optional[Live] = None
        self.enabled = True
        self._lock = threading.RLock()

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Enter the alternate screen and begin live rendering."""
        if not self.enabled or self._live is not None:
            return
        self._live = Live(
            self._render(), console=self.console, refresh_per_second=6, screen=True
        )
        self._live.start()

    def stop(self) -> None:
        """Leave the alternate screen and restore the normal console."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    def refresh(self) -> None:
        """Redraw the dashboard with the current state."""
        with self._lock:
            if self._live is not None:
                self._live.update(self._render())

    # ---- state updates -----------------------------------------------------

    def set_plan(self, steps: List[Dict[str, str]]) -> None:
        """Set the pending task plan; marks all steps as pending."""
        self._plan = [{"label": f"{s.get('agent', 'executor')}: {s.get('task', '')}", "status": "ple"} for s in steps]
        self._status = f"{len(self._plan)} step(s) planned" if self._plan else "no tools needed"
        self.refresh()

    def mark_step(self, index: int, status: str, note: str = "") -> None:
        """Set the run state of plan step ``index``."""
        if 0 <= index < len(self._plan):
            self._plan[index]["status"] = status
            if note:
                self._plan[index]["note"] = note
        self._status = "executing plan…"
        self.refresh()

    def set_status(self, text: str) -> None:
        """Set the footer status line."""
        self._status = text
        self.refresh()

    def add_log(self, line: str) -> None:
        """Append a captured log line to the tail."""
        self._log.append(line)
        self._log = self._log[-6:]
        self.refresh()

    def accumulate_usage(self, counts: Dict[str, int]) -> None:
        """Add token counts from one generation."""
        for key in self._usage:
            self._usage[key] += counts.get(key, 0)
        self.refresh()

    def note_request(self, agent_name: str) -> None:
        """Record that the named agent made an LLM request (live hook)."""
        self._requests[agent_name] = self._requests.get(agent_name, 0) + 1
        self.refresh()

    # ---- rendering ---------------------------------------------------------

    def _header(self) -> Panel:
        title = Text.assemble(("pico", "bold cyan"), " — your day-to-day assistant")
        meta = Text(
            f"provider={self.provider or '?'}  model={self.model or '?'}  "
            f"tokens: {self._usage['prompt']} in / {self._usage['completion']} out / {self._usage['total']} total"
        )
        return Panel(Group(title, meta), border_style="cyan", title="[bold cyan]status[/bold cyan]")

    def _plan_table(self) -> Table:
        table = Table(title="Task plan", expand=True, box=None)
        table.add_column("Status", width=10)
        table.add_column("Step", overflow="fold")
        for step in self._plan:
            marker = _STATUS_BY_NAME.get(step["status"], _PENDING)
            note = step.get("note", "")
            label = f"{step['label']}" if not note else f"{step['label']}  [dim]({note})[/dim]"
            table.add_row(marker, label)
        if not self._plan:
            table.add_row(_PENDING, "No plan yet — pico is planning…")
        return table

    def _activity_table(self) -> Table:
        table = Table(title="Activity", expand=True, box=None)
        table.add_column("Agent", width=12)
        table.add_column("Requests", justify="right")
        for name, count in sorted(self._requests.items()):
            table.add_row(name, str(count))
        if not self._requests:
            table.add_row("[dim]…[/dim]", "0")
        return table

    def _memory_lines(self) -> List[str]:
        if self.memory is None:
            return ["memory not wired"]
        lines = [
            f"working: {len(self.memory.working.snapshot())}",
            f"episodes: {len(self.memory.episodic.episodes())}",
            f"long-term: {len(self.memory.longterm.notes())}",
            f"semantic: {self.memory.semantic.count()}",
        ]
        return lines

    def _live_group(self) -> Group:
        memory = Text("\n".join(self._memory_lines()))
        usage = Table(title="Tokens", expand=True, box=None)
        usage.add_column("Kind", width=10)
        usage.add_column("Count", justify="right")
        usage.add_row("prompt", str(self._usage["prompt"]))
        usage.add_row("completion", str(self._usage["completion"]))
        usage.add_row("total", str(self._usage["total"]))
        components: List[Any] = [usage, self._activity_table()]
        memory = Text("\n".join(self._memory_lines()))
        components.append(Panel(memory, title="Memory", border_style="blue"))
        log_panel = Panel(
            Text("\n".join(self._log) if self._log else "[dim]no log yet[/dim]"),
            title="Log tail",
            border_style="dim",
        )
        components.append(log_panel)
        return Group(*components)

    def _render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", renderable=self._header(), ratio=1),
            Layout(name="body", ratio=5),
            Layout(name="footer", renderable=Panel(self._status, border_style="magenta"), ratio=1),
        )
        layout["body"].split_row(
            Layout(name="plan", renderable=Panel(self._plan_table(), border_style="blue"), ratio=3),
            Layout(name="live", renderable=Panel(self._live_group(), border_style="green"), ratio=2),
        )
        return layout