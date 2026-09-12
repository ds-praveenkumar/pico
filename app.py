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
from brain.logging_setup import capture_logs, drained_logs, get_logger, setup_rich_logging, stop_rich_logging
from brain.memory import Memory
from brain.nvidia_client import NvidiaClient
from brain.openai_client import OpenAIClient

from agents.pico import Pico
from dashboard import Dashboard

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
    if provider == "nvidia":
        return NvidiaClient(
            provider="nvidia",
            model_name=os.getenv("NVIDIA_MODEL_ID") or "",
            api_key=os.getenv("NVIDIA_API_KEY"),
            base_url=os.getenv("NVIDIA_BASE_URL"),
        )
    raise ValueError(f"unknown PROVIDER={provider!r} (expected openai, nvidia, or cerebras)")


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


def interactive_approve(dashboard: Dashboard) -> Callable[[str, Dict[str, Any]], bool]:
    """Build an approver that pauses the dashboard to ask the master on screen."""
    def approve(name: str, args: Dict[str, Any]) -> bool:
        dashboard.stop()
        console.print(f"[bold yellow]pico wants to call[/bold yellow] {name}({args})")
        answer = input("Approve? [y/N] ").strip().lower()
        dashboard.start()
        return answer in {"y", "yes"}

    return approve


def reject_all(_name: str, _args: Dict[str, Any]) -> bool:
    """Refuse every tool call (single-shot without --yes)."""
    return False


def run_task(
    pico: Pico,
    llm: BaseLLM,
    task: str,
    plan: PlanView,
    stats: SessionStats,
    dashboard: Dashboard,
) -> None:
    """Run one task through pico behind the dashboard and print its results."""
    dashboard.start()
    capture_logs(True)

    pump_stop = threading.Event()
    pump = threading.Thread(target=_pump_logs, args=(dashboard, pump_stop), daemon=True)
    pump.start()

    try:
        reply = pico.run(task)
    finally:
        dashboard.stop()
        capture_logs(False)
        pump_stop.set()
        pump.join(timeout=1.0)

    print_reply(reply)
    print_plan_summary(plan)
    console.print(stats.line(llm.usage))


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

    def on_generate(agent_name: str) -> None:
        dashboard.note_request(agent_name)
        dashboard.accumulate_usage(llm.last_generation)
        stats.note_request(agent_name)

    pico.on_plan = dashboard.set_plan
    pico.on_step = dashboard.mark_step
    pico.on_generate = on_generate


def _compose(first: Callable, second: Callable) -> Callable:
    """Return a callable that runs both callbacks in order."""

    def combined(*args: Any, **kwargs: Any) -> None:
        first(*args, **kwargs)
        second(*args, **kwargs)

    return combined


def run_repl(pico: Pico, llm: BaseLLM, plan: PlanView, stats: SessionStats, dashboard: Dashboard) -> None:
    """Run the interactive supervised loop."""
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
        run_task(pico, llm, task, plan, stats, dashboard)


def run_single(pico: Pico, llm: BaseLLM, task: str, plan: PlanView, stats: SessionStats, dashboard: Dashboard) -> None:
    """Run one task and exit."""
    run_task(pico, llm, task, plan, stats, dashboard)


def main() -> None:
    parser = argparse.ArgumentParser(description="pico — your day-to-day personal assistant")
    parser.add_argument("task", nargs="?", help="run a single task and exit")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="auto-approve tool calls in single-shot mode"
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="disable the full-screen live dashboard (plain console output)",
    )
    args = parser.parse_args()

    load_dotenv()
    if not os.getenv("NVIDIA_API_KEY") and not os.getenv("API_KEY") and not os.getenv("CEREBRAS_API_KEY"):
        raise RuntimeError("no provider API key found in environment (.env)")
    provider = (os.getenv("PROVIDER") or "nvidia").strip().lower()
    model = os.getenv("NVIDIA_MODEL_ID") or os.getenv("MODEL_ID") or os.getenv("CEREBRAS_MODEL_ID") or ""
    logger.info(
        "[bold green]Provider ready[/bold green]: %s (model=%s)", provider, model
    )

    llm = build_client()
    memory = memory_from_env()
    dashboard = Dashboard(console, provider=provider, model=model, memory=memory)
    if args.plain:
        dashboard.enabled = False

    plan = PlanView()
    stats = SessionStats()
    pico = Pico(llm=llm, approve=interactive_approve(dashboard) if not args.task else (None if args.yes else reject_all), memory=memory)
    wire_handlers(pico, llm, dashboard, plan, stats)

    if args.task:
        run_single(pico, llm, args.task, plan, stats, dashboard)
        return

    run_repl(pico, llm, plan, stats, dashboard)


if __name__ == "__main__":
    listener = setup_rich_logging()
    try:
        main()
    finally:
        stop_rich_logging()