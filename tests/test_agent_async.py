"""Async agent-layer tests: run_async, compaction, and cooperative cancellation."""

import asyncio
from types import SimpleNamespace

import pytest

from agents.cancellation import CancellationToken, TaskCancelled
from agents.executor import Executor
from agents.pico import Pico
from brain.memory import Memory
from conftest import FakeLLM

_ZERO_USAGE = {"prompt": 0, "completion": 0, "total": 0}


async def test_executor_run_async_returns_final_text():
    executor = Executor(name="executor", llm=FakeLLM())
    out = await executor.run_async("Read README.md")
    assert "tool task done" in out


async def test_executor_async_messages_contain_tool_result():
    executor = Executor(name="executor", llm=FakeLLM())
    await executor.run_async("Read README.md")
    roles = [m.get("role") for m in executor.history]
    assert "assistant" in roles
    assert "tool" in roles
    assert executor.llm.tools


async def test_pico_async_delegates_and_summarizes(tmp_path):
    llm = FakeLLM()
    pico = Pico(llm=llm, memory=Memory(dir_path=tmp_path))
    plans, steps = [], []
    pico.on_plan = lambda p: plans.append(p)
    pico.on_step = lambda *a: steps.append(a)
    reply = await pico.run_async("read the README.md file and tell me what it says")
    assert "Report:" in reply
    assert plans[0] == [{"agent": "executor", "task": "Read README.md and summarize"}]
    assert any(s[1] == "running" for s in steps)
    assert any(s[1] == "done" for s in steps)


async def test_run_async_honours_cancellation_token_before_start():
    token = CancellationToken()
    token.cancel()
    executor = Executor(name="executor", llm=FakeLLM(), cancel_token=token)
    with pytest.raises(TaskCancelled):
        await executor.run_async("Read README.md")


async def test_pico_async_cancelled_between_steps(tmp_path):
    llm = FakeLLM()
    pico = Pico(llm=llm, memory=Memory(dir_path=tmp_path))
    token = CancellationToken()
    for obj in (pico, pico.executor, pico.researcher):
        obj.cancel_token = token

    original = pico.executor.run_async

    async def cancel_after_first_step(task: str) -> str:
        result = await original(task)
        token.cancel()
        return result

    pico.executor.run_async = cancel_after_first_step  # type: ignore[assignment]
    with pytest.raises(TaskCancelled):
        await pico.run_async("read the README.md file and tell me what it says")


class _RecordingLLM(FakeLLM):
    """FakeLLM that snapshots every message list it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.all_messages: list[list[dict]] = []
        self._prompt = 10
        self._completion = 5

    def generate(self, messages=None, **kwargs):  # noqa: D401
        if messages:
            self.all_messages.append([dict(m) for m in messages])
        self.remember_usage(prompt=self._prompt, completion=self._completion)
        return super().generate(messages=messages, **kwargs)


def _has_compaction_summary(llm: _RecordingLLM) -> bool:
    for messages in llm.all_messages:
        if any("## Compaction summary" in str(m.get("content", "")) for m in messages):
            return True
    return False


async def test_async_compaction_folds_history(tmp_path):
    llm = _RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.compaction_threshold = 5
    out = await executor.run_async("Read README.md")
    assert "tool task done" in out
    assert _has_compaction_summary(llm)


async def test_async_run_resets_compaction_state(tmp_path):
    llm = _RecordingLLM()
    executor = Executor(name="executor", llm=llm, memory=Memory(dir_path=tmp_path))
    executor.compaction_threshold = 5
    await executor.run_async("Read README.md")
    llm.all_messages.clear()
    await executor.run_async("Read README.md")
    assert _has_compaction_summary(llm)