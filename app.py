"""pico CLI: an interactive supervised REPL and a single-shot runner.

Both modes run tasks behind a live rich TUI dashboard that shows the task plan,
token usage, per-agent activity, memory sizes, and a log tail. Completed plans
are summarized after each task. Pass ``--plain`` to disable the full-screen
dashboard (e.g. in a terminal that cannot take over the screen).

Usage:
    python app.py                  # interactive REPL
    python app.py "your task"      # run one task and exit
    python app.py "your task" -y   # single-shot, auto-approve tool calls
    python app.py --plain "task"   # single-shot without the live dashboard
    python app.py --tui            # Textual interface (home/history/memory/settings)
"""

import argparse
import os
import threading
from typing import Any, Callable, Dict, List

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from brain.base_llm import BaseLLM
from brain.cerebras_client import CerebrasClient
from brain.groq_client import GroqClient
from brain.logging_setup import capture_logs, drained_logs, get_logger, setup_rich_logging, stop_rich_logging
from brain.memory import Memory
from brain.nvidia_client import NvidiaClient
from brain.openai_client import OpenAIClient
from brain.openrouter_client import OpenRouterClient

from agents.approval import auto_approve as _auto_approve, bash_needs_approval as _bash_needs_approval
from agents.pico import Pico
from agents.tools import ask as ask_tools
from ui import Dashboard

logger = get_logger(__name__)
console = Console()

_PENDING = "[cyan]●[/cyan] pending"
_RUNNING = "[yellow]▶[/yellow] running"
_DONE = "[green]✓[/green] done"
_FAILED = "[red]✗[/red] failed"


def build_client() -> BaseLLM:
    """Build the LLM client for the provider named in the environment."""
    provider = (os.getenv("PROVIDER") or "nvidia").strip().lower()
    if provider == "openai":
        return OpenAIClient(
            provider="openai",
            model_name=os.getenv("MODEL_ID") or "",
            api_key=os.getenv("API_KEY"),
        )
    if provider == "cerebras":
        return CerebrasClient(
            provider="cerebras",
            model_name=os.getenv("CEREBRAS_MODEL_ID") or "",
            api_key=os.getenv("CEREBRAS_API_KEY"),
            base_url=os.getenv("CEREBRAS_BASE_URL"),
        )
    if provider == "groq":
        return GroqClient(
            provider="groq",
            model_name=os.getenv("GROQ_MODEL_ID") or os.getenv("GROK_MODEL_ID") or "",
            api_key=os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY"),
            base_url=os.getenv("GROQ_BASE_URL") or os.getenv("GROK_BASE_URL"),
        )
    if provider == "nvidia":
        return NvidiaClient(
            provider="nvidia",
            model_name=os.getenv("NVIDIA_MODEL_ID") or "",
            api_key=os.getenv("NVIDIA_API_KEY"),
            base_url=os.getenv("NVIDIA_BASE_URL"),
        )
    if provider == "openrouter":
        return OpenRouterClient(
            provider="openrouter",
            model_name=os.getenv("OPENROUTER_MODEL_ID") or "",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_BASE_URL"),
        )
    raise ValueError(f"unknown PROVIDER={provider!r} (expected openai, nvidia, cerebras, groq, or openrouter)")


def memory_from_env() -> Memory:
    """Build the memory layers, honoring PICO_MEMORY_PATH if set."""
    base = os.getenv("PICO_MEMORY_PATH")
    return Memory(dir_path=base) if base else Memory()


class PlanView:
    """Records a task plan and its step statuses for post-run display."""

    def __init__(self) -> None:
        self.steps: List[Dict[str, str]] = []

    def set_plan(self, steps: List[Dict[str, str]]) -> None:
        """Adopt a parsed step list, all steps pending."""
        self.steps = [{"agent": str(s.get("agent", "executor")), "task": str(s.get("task", "")), "status": "pending"} for s in steps]

    def mark_step(self, index: int, status: str, note: str = "") -> None:
        """Set the run state of one plan step."""
        if 0 <= index < len(self.steps):
            self.steps[index]["status"] = status

    def table(self) -> Table:
        """Render the plan (with run states) as a rich table."""
        table = Table(title="Task plan", expand=True, box=None)
        table.add_column("Status", width=10)
        table.add_column("Step")
        marker = {"pending": _PENDING, "running": _RUNNING, "done": _DONE, "failed": _FAILED}
        for step in self.steps:
            table.add_row(marker.get(step["status"], _PENDING), f"{step['agent']}: {step['task']}")
        return table


class SessionStats:
    """Cumulative token usage and LLM request counts for the whole session."""

    def __init__(self) -> None:
        self.requests = 0

    def note_request(self, _agent_name: str) -> None:
        self.requests += 1

    def line(self, usage: Dict[str, int]) -> str:
        return (
            f"[bold cyan]◄ session ▸[/bold cyan] tokens: "
            f"{usage['prompt']} prompt / {usage['completion']} completion / {usage['total']} total "
            f"· {self.requests} LLM request(s)"
        )


def print_reply(text: str) -> None:
    """Render pico's reply as markdown inside a styled panel."""
    console.print(Panel(Markdown(text), title="[bold cyan]pico[/bold cyan]", border_style="cyan"))


def print_plan_summary(plan: PlanView) -> None:
    """Show the completed task plan as a table."""
    console.print(Panel(plan.table(), border_style="green"))


def smart_approve(dashboard: Dashboard) -> Callable[[str, Dict[str, Any]], bool]:
    """Build an approver that only asks for higher-risk tool calls.

    Read-only tools, project-confined writes, browsing, and trivial commands
    (``date``, ``echo``, ``pwd``, ``ls``, ``git status``, ...) run automatically;
    anything unclear asks the master — inside the TUI when it is live, on
    console otherwise.
    """
    def approve(name: str, args: Dict[str, Any]) -> bool:
        if _auto_approve(name, args):
            logger.info("[dim]auto-approved[/dim] %s(%s)", name, args)
            return True
        question = f"pico wants to call {name}({args}) — approve?"
        if dashboard.enabled and dashboard._live is not None:
            answer = dashboard.ask_yes_no(question)
        else:
            console.print(f"[bold yellow]pico wants to call[/bold yellow] {name}({args})")
            answer = input("Approve? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    return approve


def run_task(
    pico: Pico,
    llm: BaseLLM,
    task: str,
    plan: PlanView,
    stats: SessionStats,
    dashboard: Dashboard,
    persistent: bool = False,
) -> bool:
    """Run one task through pico behind the dashboard and print its results.

    Returns True on success, False if pico raised (so callers can decide how
    to keep going). In ``persistent`` mode the Live dashboard is already running
    and owns the screen: the answer is routed back into the TUI instead of the
    console. Log capture and the log pump are always restored.
    """
    if not persistent:
        dashboard.start()
        capture_logs(True)

    pump_stop = threading.Event()
    pump = threading.Thread(target=_pump_logs, args=(dashboard, pump_stop), daemon=True)
    pump.start()

    failed = False
    reply = ""
    dashboard.set_running(True)
    try:
        try:
            reply = pico.run(task)
        except Exception as exc:  # noqa: BLE001 - a provider/tool failure must not kill the app
            failed = True
            logger.error(f"[bold red]Task failed[/bold red]: {exc}")
    finally:
        dashboard.set_running(False)
        if not persistent:
            dashboard.stop()
            capture_logs(False)
        pump_stop.set()
        pump.join(timeout=1.0)

    if failed:
        message = "pico hit an error and could not finish the task.\nCheck the log above for details (network, provider, or tool)."
        if persistent:
            dashboard.show_reply(message, "task failed")
        else:
            console.print(Panel(message, title="[bold red]task failed[/bold red]", border_style="red"))
        return False

    if persistent:
        dashboard.show_reply(reply, f"{task.strip()[:60]} · {stats.line(llm.usage)}")
        return True

    print_reply(reply)
    print_plan_summary(plan)
    console.print(stats.line(llm.usage))
    return True


def _pump_logs(dashboard: Dashboard, stop: threading.Event) -> None:
    """Background thread that feeds captured log lines into the dashboard."""
    while not stop.wait(0.25):
        for line in drained_logs():
            dashboard.add_log(line)


def wire_handlers(
    pico: Pico,
    llm: BaseLLM,
    dashboard: Dashboard,
    plan: PlanView,
    stats: SessionStats,
) -> None:
    """Connect pico's hooks to the dashboard, plan view, and session stats."""
    dashboard.set_plan = _compose(dashboard.set_plan, plan.set_plan)
    dashboard.mark_step = _compose(dashboard.mark_step, plan.mark_step)

    def on_generate(agent_name: str, content: str = "") -> None:
        dashboard.note_request(agent_name)
        dashboard.accumulate_usage(llm.last_generation)
        stats.note_request(agent_name)
        if content:
            dashboard.set_output(agent_name, content)

    def on_tool(agent_name: str, content: str = "") -> None:
        if content:
            dashboard.set_output(agent_name, content)

    pico.on_plan = dashboard.set_plan
    pico.on_step = dashboard.mark_step
    pico.on_generate = on_generate
    pico.on_tool = on_tool


def _compose(first: Callable, second: Callable) -> Callable:
    """Return a callable that runs both callbacks in order."""

    def combined(*args: Any, **kwargs: Any) -> None:
        first(*args, **kwargs)
        second(*args, **kwargs)

    return combined


def run_repl(pico: Pico, llm: BaseLLM, plan: PlanView, stats: SessionStats, dashboard: Dashboard) -> None:
    """Run the interactive supervised loop (plain console, no full-screen TUI)."""
    console.print("[bold cyan]pico at your service.[/bold cyan] Type 'exit' to quit.")
    while True:
        try:
            task = input("pico> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not task:
            continue
        if task.lower() in {"exit", "quit", "q"}:
            break
        console.print(f"[dim]running: {task}[/dim] (approvals only for higher-risk calls)")
        run_task(pico, llm, task, plan, stats, dashboard)


def run_tui_repl(
    pico: Pico,
    llm: BaseLLM,
    plan: PlanView,
    stats: SessionStats,
    dashboard: Dashboard,
) -> None:
    """Run a persistent full-screen REPL: the dashboard and its input line own
    the terminal, and the master types each task on screen."""
    dashboard.start()
    capture_logs(True)
    pump_stop = threading.Event()
    pump = threading.Thread(target=_pump_logs, args=(dashboard, pump_stop), daemon=True)
    pump.start()
    try:
        while True:
            try:
                task = dashboard.read_line("pico> ")
            except KeyboardInterrupt:
                console.print()
                break
            except EOFError:
                break
            task = task.strip()
            if not task:
                continue
            if task.lower() in {"exit", "quit", "q"}:
                break
            if task.lower() in {"clear", "cls"}:
                plan.steps = []
                dashboard.set_plan([])
                dashboard.set_status("cleared")
                continue
            dashboard.set_status("working on your task…")
            run_task(pico, llm, task, plan, stats, dashboard, persistent=True)
            dashboard.set_status("ready for your next task — type below")
    finally:
        pump_stop.set()
        pump.join(timeout=1.0)
        capture_logs(False)
        dashboard.stop()


def run_single(pico: Pico, llm: BaseLLM, task: str, plan: PlanView, stats: SessionStats, dashboard: Dashboard) -> int:
    """Run one task and exit; return the process exit code."""
    ok = run_task(pico, llm, task, plan, stats, dashboard)
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="pico — your day-to-day personal assistant")
    parser.add_argument("task", nargs="?", help="run a single task and exit")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="auto-approve tool calls in single-shot mode"
    )
    view = parser.add_mutually_exclusive_group()
    view.add_argument(
        "--plain",
        action="store_true",
        help="disable the full-screen live dashboard (plain console output)",
    )
    view.add_argument(
        "--tui",
        action="store_true",
        help="use the Textual interface (home, history, memory, settings, approvals)",
    )
    args = parser.parse_args()

    load_dotenv()
    if (
        not os.getenv("NVIDIA_API_KEY")
        and not os.getenv("API_KEY")
        and not os.getenv("CEREBRAS_API_KEY")
        and not (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY"))
        and not os.getenv("OPENROUTER_API_KEY")
    ):
        raise RuntimeError("no provider API key found in environment (.env)")
    provider = (os.getenv("PROVIDER") or "nvidia").strip().lower()
    model = (
        os.getenv("NVIDIA_MODEL_ID")
        or os.getenv("MODEL_ID")
        or os.getenv("CEREBRAS_MODEL_ID")
        or os.getenv("GROQ_MODEL_ID")
        or os.getenv("GROK_MODEL_ID")
        or os.getenv("OPENROUTER_MODEL_ID")
        or ""
    )
    logger.info(
        "[bold green]Provider ready[/bold green]: %s (model=%s)", provider, model
    )

    llm = build_client()
    memory = memory_from_env()

    if args.tui:
        from ui.textual_app import PicoTUI

        from ui.tui_history import HistoryStore

        if args.yes:
            os.environ["PICO_AUTO_APPROVE"] = "1"
        app = PicoTUI(
            llm=llm,
            memory=memory,
            provider=provider,
            model=model,
            history=HistoryStore(),
            approve=None,
            task=args.task,
        )
        app.run()
        return

    dashboard = Dashboard(console, provider=provider, model=model, memory=memory)
    if args.plain:
        dashboard.enabled = False

    plan = PlanView()
    stats = SessionStats()
    if args.yes:
        os.environ["PICO_AUTO_APPROVE"] = "1"
    pico = Pico(llm=llm, approve=(None if args.yes else smart_approve(dashboard)), memory=memory)
    ask_tools.set_master_prompt(dashboard.ask_text)
    wire_handlers(pico, llm, dashboard, plan, stats)

    if args.task:
        code = run_single(pico, llm, args.task, plan, stats, dashboard)
        raise SystemExit(code)

    if args.plain:
        run_repl(pico, llm, plan, stats, dashboard)
    else:
        run_tui_repl(pico, llm, plan, stats, dashboard)


if __name__ == "__main__":
    listener = setup_rich_logging()
    try:
        main()
    finally:
        stop_rich_logging()