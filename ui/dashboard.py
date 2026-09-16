"""Full-screen rich TUI for the pico CLI.

``Dashboard`` renders a Live layout showing the current task plan, live token
usage, per-agent activity, memory sizes, streamed LLM output, and a tail of
captured log lines while a task runs. It also owns a persistent full-screen
interactive mode: the last answer and a `pico> ` input line are rendered on
screen, so the master can type their next task directly inside the TUI.
"""

import codecs
import os
import sys
import termios
import threading
import time
import tty
from typing import Any, Dict, List, Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ui.fontsize import apply_font_size

_PENDING = "[cyan]●[/cyan] pending"
_RUNNING = "[yellow]▶[/yellow] running"
_DONE = "[green]✓[/green] done"
_FAILED = "[red]✗[/red] failed"

_STATUS_BY_NAME = {"ple": _PENDING, "running": _RUNNING, "done": _DONE, "failed": _FAILED}

# Braille dot spinner for the executing animation (one frame per render tick).
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_PERIOD = 0.15  # seconds per frame (matches the 6 fps Live refresh)


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
        self._output: List[str] = []
        self._usage: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self._requests: Dict[str, int] = {}
        self._live: Optional[Live] = None
        self.enabled = True
        self._lock = threading.RLock()
        self._reply = ""
        self._reply_meta = ""
        self._running = False
        self._frame = 0
        self._anim_thread: Optional[threading.Thread] = None
        self._input_prompt = ""
        self._input_buffer = ""
        self._input_active = False

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Enter the alternate screen and begin live rendering."""
        if not self.enabled or self._live is not None:
            return
        apply_font_size()
        self._live = Live(
            self._render(), console=self.console, refresh_per_second=6, screen=True
        )
        self._live.start()

    def stop(self) -> None:
        """Leave the alternate screen and restore the normal console."""
        self._running = False
        if self._anim_thread is not None:
            self._anim_thread.join(timeout=_SPINNER_PERIOD * 2)
            self._anim_thread = None
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

    def set_output(self, agent_name: str, text: str) -> None:
        """Stream an LLM generation or tool result into the dashboard."""
        if not text:
            return
        stripped = text.strip()
        if not stripped or stripped.lower() in {"none", "null", "n/a"}:
            return
        max_len = 400
        if len(text) > max_len:
            text = text[:max_len] + "…"
        self._output.append(f"{agent_name} › {text}")
        self._output = self._output[-2:]
        self.refresh()

    # ---- persistence / chaining -------------------------------------------

    def show_reply(self, text: str, meta: str = "") -> None:
        """Show the master a completed answer inside the TUI."""
        self._reply = text
        self._reply_meta = meta
        self._running = False
        self.refresh()

    def set_running(self, running: bool) -> None:
        """Mark the dashboard as working on a task or idle."""
        self._running = running
        if running:
            self._start_animation()
        self.refresh()

    def _spinner_char(self) -> str:
        """Return the current spinner frame for the running animation."""
        return _SPINNER[self._frame % len(_SPINNER)]

    def _start_animation(self) -> None:
        """Start (or reuse) the background thread that animates while running."""
        if self._anim_thread is not None and self._anim_thread.is_alive():
            return
        self._anim_thread = threading.Thread(target=self._anim_loop, daemon=True)
        self._anim_thread.start()

    def _anim_loop(self) -> None:
        """Advance the spinner frame and repaint until the task stops."""
        while self.enabled and self._running and self._live is not None:
            time.sleep(_SPINNER_PERIOD)
            with self._lock:
                self._frame += 1
            self.refresh()

    # ---- in-TUI input -----------------------------------------------------

    def read_line(self, prompt: str = "") -> str:
        """Read one line of input, rendered inside the live TUI.

        On a real terminal this switches stdin to character mode (no echo) so
        keystrokes update the input line in the footer; on non-tty stdin it
        falls back to plain :func:`input`.
        """
        self._input_prompt = prompt
        self._input_buffer = ""
        self._input_active = True
        self.refresh()

        if not sys.stdin.isatty() or not hasattr(termios, "tcgetattr"):
            try:
                line = input(prompt)
            except EOFError:
                line = ""
            self._finish_input()
            return line

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        chars: List[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            tty.setcbreak(fd, termios.TCSANOW)
            while True:
                chunk = os.read(fd, 1)
                if chunk == b"\x03":
                    raise KeyboardInterrupt
                if chunk == b"\x04":
                    raise EOFError
                if chunk in (b"\r", b"\n"):
                    break
                if chunk in (b"\x7f", b"\x08"):
                    if chars:
                        chars.pop()
                        self._input_buffer = "".join(chars)
                        self.refresh()
                    continue
                char = decoder.decode(chunk)
                if char:
                    chars.append(char)
                    self._input_buffer = "".join(chars)
                    self.refresh()
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)
            self._finish_input()
        return "".join(chars)

    def ask_yes_no(self, question: str) -> bool:
        """Ask a yes/no question inside the TUI and return the decision."""
        answer = self.read_line(f"{question} [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    def ask_text(self, question: str, max_len: int = 110) -> str:
        """Ask the master a free-text question.

        On the live TUI the question is rendered in the input footer so it stays
        visible without corrupting the screen; outside the live view it degrades
        to a plain console prompt.
        """
        if self.enabled and self._live is not None:
            preview = question if len(question) <= max_len else question[: max_len - 1] + "…"
            return self.read_line(f"{preview}\n[master]>\n> ").strip()
        print(question)
        try:
            return input("> ").strip()
        except EOFError:
            return ""

    def _finish_input(self) -> None:
        with self._lock:
            self._input_active = False
            self._input_prompt = ""
            self._input_buffer = ""
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

    def _output_text(self) -> Text:
        """Render the streamed output with the agent prefix highlighted."""
        parts = [
            Text.assemble((f"{agent} › ", "bold"), body)
            for (agent, _, body) in (entry.partition(" › ") for entry in self._output)
        ]
        if not parts:
            return Text("no output yet — pico is thinking…", style="dim")
        return Text("\n\n").join(parts)

    def _live_group(self) -> Group:
        usage = Table(title="Tokens", expand=True, box=None)
        usage.add_column("Kind", width=10)
        usage.add_column("Count", justify="right")
        usage.add_row("prompt", str(self._usage["prompt"]))
        usage.add_row("completion", str(self._usage["completion"]))
        usage.add_row("total", str(self._usage["total"]))
        components: List[Any] = [
            Panel(
                self._output_text(),
                title="Live output",
                border_style="green",
            ),
            usage,
            self._activity_table(),
        ]
        memory = Text("\n".join(self._memory_lines()))
        components.append(Panel(memory, title="Memory", border_style="blue"))
        log_panel = Panel(
            Text("\n".join(self._log) if self._log else "[dim]no log yet[/dim]"),
            title="Log tail",
            border_style="dim",
        )
        components.append(log_panel)
        return Group(*components)

    def _answer_panel(self) -> Panel:
        """Show the master's latest answer (or a hint while idle)."""
        if self._running and self._reply:
            content: Any = Text(f"{self._reply}\n", style="dim") + Text(self._reply_meta or "", style="dim")
        elif self._reply:
            content = Group(
                Markdown(self._reply),
                Text(self._reply_meta or "", style="dim"),
            )
        else:
            hint = "pico is working…" if self._running else "Type a task below and press Enter."
            content = Text(f"{self._spinner_char()} " + hint if self._running else hint, style="dim")
        return Panel(content, title="[bold cyan]pico[/bold cyan]", border_style="cyan")

    def _footer(self) -> Panel:
        """Render the status bar or the live input line."""
        if self._input_active:
            line = f"{self._input_prompt}{self._input_buffer}▌"
            return Panel(Text(line), border_style="cyan", title="[bold cyan]pico[/bold cyan]")
        status = self._status
        if self._running:
            status = f"{self._spinner_char()} {self._status}"
        return Panel(status, border_style="magenta")

    def _render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", renderable=self._header(), ratio=1),
            Layout(name="answer", renderable=self._answer_panel(), ratio=3),
            Layout(name="body", ratio=6),
            Layout(name="footer", renderable=self._footer(), ratio=1),
        )
        layout["body"].split_row(
            Layout(name="plan", renderable=Panel(self._plan_table(), border_style="blue"), ratio=3),
            Layout(name="live", renderable=Panel(self._live_group(), border_style="green"), ratio=2),
        )
        return layout