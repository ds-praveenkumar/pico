"""Shared fixtures: project-path setup and a network-free fake LLM."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def project_root() -> Path:
    """Return the pico project root."""
    return PROJECT_ROOT


def fake_message(content=None, tool_calls=None):
    """Build a fake chat-completion message object."""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeLLM:
    """Deterministic stand-in for a chat client; never touches the network."""

    def __init__(self):
        self.tools = None
        self.calls = []
        self._usage = {"prompt": 0, "completion": 0, "total": 0}
        self._last_generation = {"prompt": 0, "completion": 0, "total": 0}

    @property
    def usage(self):
        return dict(self._usage)

    @property
    def last_generation(self):
        return dict(self._last_generation)

    def remember_usage(self, prompt=0, completion=0, total=None):
        """Simulate a generation's token usage for a hand-rolled fake."""
        if total is None:
            total = prompt + completion
        self._last_generation = {"prompt": prompt, "completion": completion, "total": total}
        for key in self._usage:
            self._usage[key] += self._last_generation[key]

    def generate(self, messages=None, **kwargs):
        self.calls.append(messages[-1]["content"])
        last_user = messages[-1]["content"]

        if any(msg.get("role") == "assistant" and msg.get("tool_calls") for msg in messages):
            return fake_message("tool task done")

        if "Planning:" in last_user:
            return fake_message('[{"agent": "executor", "task": "Read README.md and summarize"}]')

        if last_user.startswith("I am compressing a tool-working conversation"):
            return fake_message("COMPACTED:: task progress and findings preserved.")

        if last_user.startswith("Summarize the following sub-agent results"):
            return fake_message("Report: the task is complete.")

        if "Read README.md" in last_user:
            return fake_message(
                None,
                [
                    SimpleNamespace(
                        id="call_1",
                        function=SimpleNamespace(name="file_read", arguments='{"path": "README.md"}'),
                    )
                ],
            )

        return fake_message("ok")


@pytest.fixture
def fake_llm() -> FakeLLM:
    """Provide a fresh FakeLLM per test."""
    return FakeLLM()