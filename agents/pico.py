"""Pico orchestrator: plans tasks, delegates to sub-agents, and summarizes results."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.logging_setup import get_logger

from agents.base_agent import BaseAgent
from agents.cancellation import CancellationToken, TaskCancelled
from agents.executor import Executor
from agents.researcher import DEFAULT_RESEARCHER_TURNS, Researcher

logger = get_logger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.md"

_PLAN_INSTRUCTION = (
    "Break the master's request into 1-4 concrete steps. Reply with ONLY a JSON list, "
    'no prose, where each item is: {"agent": "executor" or "researcher", "task": "...one imperative task..."}. '
    "Use \"researcher\" for browsing/verifying information on the web and \"executor\" "
    "for safe file/shell work. If the request needs no tools, reply with an empty list []. "
    "Never answer time-sensitive questions (today's date, current time) from memory: "
    'plan an executor step such as {"agent": "executor", "task": "find out today\'s date and time with the current_date tool"} '
    "instead of answering directly. For today's news / latest-headlines requests, plan an "
    'executor step such as {"agent": "executor", "task": "fetch today\'s top news with the latest_news tool"} '
    "instead of answering from memory."
)


def parse_plan(text: str) -> List[Dict[str, str]]:
    """Parse a JSON step list from LLM output; tolerate code fences and noise."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    steps: List[Dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            agent = str(item.get("agent") or "executor")
            task = str(item.get("task") or "").strip()
            if task:
                steps.append({"agent": agent, "task": task})
    return steps


class Pico(BaseAgent):
    """Orchestrator that plans a task, delegates to sub-agents, and summarizes."""

    def __init__(
        self,
        llm: Any,
        approve: Optional[Any] = None,
        executor: Optional[Executor] = None,
        researcher: Optional[Researcher] = None,
        memory: Optional[Any] = None,
        max_turns: int = 6,
        cancel_token: Optional[CancellationToken] = None,
    ) -> None:
        super().__init__(
            name="pico", llm=llm, approve=approve, max_turns=max_turns, memory=memory, tools=[], cancel_token=cancel_token
        )
        self.executor = executor or Executor(
            name="executor", llm=llm, approve=approve, max_turns=max_turns, memory=memory, cancel_token=cancel_token
        )
        self.researcher = researcher or Researcher(
            name="researcher", llm=llm, approve=approve,
            max_turns=max(max_turns, DEFAULT_RESEARCHER_TURNS), memory=memory, cancel_token=cancel_token
        )
        self.executor.on_generate = self._forward_generate
        self.researcher.on_generate = self._forward_generate
        self.executor.on_tool = self._forward_tool
        self.researcher.on_tool = self._forward_tool
        self.on_plan: Optional[Any] = None
        self.on_step: Optional[Any] = None
        self.on_tool: Optional[Any] = None

    def system_instructions(self) -> str:
        return _read_system_prompt()

    def run(self, task: str) -> str:
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()
        plan_text = self._ask(self._with_plan_instruction(task))
        steps = parse_plan(plan_text)
        if not steps:
            return self._ask(task)
        if self.on_plan is not None:
            self.on_plan(steps)
        combined_parts: List[str] = []
        for index, step in enumerate(steps):
            agent = self._sub_agent(step["agent"])
            label = f"{step['agent']}: {step['task']}"
            if agent is None:
                combined_parts.append(f"[{step['agent']}] unknown agent; step skipped")
                if self.on_step is not None:
                    self.on_step(index, "failed", label)
                continue
            logger.info(f"[bold cyan]Delegating[/bold cyan] {step['agent']}: {step['task']}")
            if self.on_step is not None:
                self.on_step(index, "running", label)
            try:
                outcome = agent.run(step["task"])
                combined_parts.append(f"[{step['agent']}] {outcome}")
                if self.on_step is not None:
                    self.on_step(index, "done", label)
            except Exception as exc:  # noqa: BLE001 - a failing subtask must not kill the whole run
                logger.error(f"[bold red]Sub-agent failed[/bold red]: {step['agent']} -> {exc}")
                combined_parts.append(f"[{step['agent']}] failed: {exc}")
                if self.on_step is not None:
                    self.on_step(index, "failed", label)
            if self.cancel_token is not None:
                self.cancel_token.raise_if_cancelled()
        combined = "\n\n".join(combined_parts)
        summary = self._summarize(task, combined)
        self._remember(task, combined)
        return summary

    async def run_async(self, task: str) -> str:
        """Async orchestration: plan, delegate to sub-agents, and summarize.

        Mirrors :meth:`run` for event-driven interfaces (Textual): planning and
        summarization use :meth:`_ask_async` and sub-agents run through
        :meth:`BaseAgent.run_async`, so the UI event loop stays reactive and
        cancellation is honored between every step.
        """
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()
        plan_text = await self._ask_async(self._with_plan_instruction(task))
        steps = parse_plan(plan_text)
        if not steps:
            return await self._ask_async(task)
        if self.on_plan is not None:
            self.on_plan(steps)
        combined_parts: List[str] = []
        for index, step in enumerate(steps):
            agent = self._sub_agent(step["agent"])
            label = f"{step['agent']}: {step['task']}"
            if agent is None:
                combined_parts.append(f"[{step['agent']}] unknown agent; step skipped")
                if self.on_step is not None:
                    self.on_step(index, "failed", label)
                continue
            logger.info(f"[bold cyan]Delegating[/bold cyan] {step['agent']}: {step['task']}")
            if self.on_step is not None:
                self.on_step(index, "running", label)
            try:
                outcome = await agent.run_async(step["task"])
                combined_parts.append(f"[{step['agent']}] {outcome}")
                if self.on_step is not None:
                    self.on_step(index, "done", label)
            except TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - a failing subtask must not kill the whole run
                logger.error(f"[bold red]Sub-agent failed[/bold red]: {step['agent']} -> {exc}")
                combined_parts.append(f"[{step['agent']}] failed: {exc}")
                if self.on_step is not None:
                    self.on_step(index, "failed", label)
            if self.cancel_token is not None:
                self.cancel_token.raise_if_cancelled()
        combined = "\n\n".join(combined_parts)
        summary = await self._summarize_async(task, combined)
        self._remember(task, combined)
        return summary

    def _forward_generate(self, agent_name: str, content: str = "") -> None:
        """Relay a sub-agent's generate hook through pico's own on_generate."""
        if self.on_generate is not None:
            self.on_generate(agent_name, content)

    def _forward_tool(self, agent_name: str, content: str = "") -> None:
        """Relay a sub-agent's tool-output hook through pico's own on_tool."""
        if self.on_tool is not None:
            self.on_tool(agent_name, content)

    def _with_plan_instruction(self, task: str) -> str:
        return f"{task}\n\nPlanning: {_PLAN_INSTRUCTION}"

    def _ask(self, task: str) -> str:
        """Single-turn LLM call (planning, direct answers, summarization)."""
        messages: List[Dict[str, str]] = []
        system_prompt = self.system_instructions()
        if self.memory is not None:
            context = self.memory.load_context() or ""
            if context:
                system_prompt = f"{system_prompt}\n\n## Notes about the master\n{context}"
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})
        response = self._generate(messages)
        return self._text_of(response)

    async def _ask_async(self, task: str) -> str:
        """Async single-turn LLM call (planning, direct answers, summarization)."""
        messages: List[Dict[str, str]] = []
        system_prompt = self.system_instructions()
        if self.memory is not None:
            context = self.memory.load_context() or ""
            if context:
                system_prompt = f"{system_prompt}\n\n## Notes about the master\n{context}"
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})
        response = await self._generate_async(messages)
        return self._text_of(response)

    @staticmethod
    def _summary_prompt(task: str, combined: str) -> str:
        """Build the summarization prompt from the original task and results."""
        return (
            "Summarize the following sub-agent results into a short, clear report for "
            f"the master. Original request: {task}\n\nSub-agent results:\n{combined}"
        )

    def _summarize(self, task: str, combined: str) -> str:
        return self._ask(self._summary_prompt(task, combined))

    async def _summarize_async(self, task: str, combined: str) -> str:
        return await self._ask_async(self._summary_prompt(task, combined))

    def _remember(self, task: str, combined: str) -> None:
        if self.memory is None:
            return
        try:
            self.memory.add_episode(
                title=task.strip()[:80] or "untitled task",
                summary=combined.strip()[:300],
            )
        except Exception as exc:  # noqa: BLE001 - memory failures must never break a task
            logger.warning(f"[bold yellow]Could not save episode[/bold yellow]: {exc}")

    def _sub_agent(self, name: str) -> Optional[BaseAgent]:
        return {"executor": self.executor, "researcher": self.researcher}.get(name)

    @property
    def usage(self) -> Dict[str, int]:
        """Combined token usage across pico and both sub-agents."""
        totals = dict(self.usage_accum)
        for agent in (self.executor, self.researcher):
            for key in totals:
                totals[key] += agent.usage.get(key, 0)
        return totals

    def _text_of(self, response: Any) -> str:
        if hasattr(response, "choices"):
            return response.choices[0].message.content or ""
        if hasattr(response, "content"):
            return response.content if isinstance(response.content, str) else str(response.content)
        return str(response)


def _read_system_prompt() -> str:
    try:
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning(f"[bold yellow]Missing system prompt[/bold yellow]: {SYSTEM_PROMPT_PATH}")
        return ""