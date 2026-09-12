"""Worker sub-agent that runs tool-based tasks and reports a summarized outcome."""

from typing import Any, Dict

from brain.logging_setup import get_logger

from agents.base_agent import BaseAgent
from agents.tools import dispatch

logger = get_logger(__name__)

_EXECUTOR_SYSTEM_PROMPT = (
    "You are pico's executor, a careful worker agent. "
    "You complete small, well-defined tool tasks for the master, Praveen. "
    "Pick the right tool for the job, call it, read its output carefully, "
    "and reply with a short, truthful summary of what was done. "
    "Never delete files, never access paths outside the project root, "
    "and never invent tool results. If a tool refuses a request, report why. "
    "Capture what you learn: save durable facts with memory_remember, keep quick "
    "session notes with memory_note, record valuable completed steps with "
    "memory_episode, and store memorable sentences with semantic_remember. Use "
    "semantic_search to recall past memories by meaning. "
    "You can also read the master's email (gmail_latest / gmail_search, read-only "
    "IMAP) and run shell commands only inside the sandbox — never ask for sandbox "
    "limits to be removed."
)


class Executor(BaseAgent):
    """Sub-agent that executes tool tasks and returns summarized results."""

    def system_instructions(self) -> str:
        return _EXECUTOR_SYSTEM_PROMPT

    def execute_tool(self, name: str, **kwargs: Any) -> Dict[str, Any]:
        """Run a single tool directly and return its raw dict result."""
        if self.approve is not None and not self.approve(name, kwargs):
            return {"ok": False, "error": "rejected by the master"}
        return dispatch(name, **kwargs)