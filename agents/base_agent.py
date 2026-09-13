"""Shared base for all pico agents.

``BaseAgent`` owns an LLM client, a tool set, and a supervised tool-calling loop.
Subclasses override :meth:`system_instructions` for their role. Every tool call
goes through an optional supervisor (``approve``) before it executes.
"""

import asyncio
import inspect
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from brain.base_llm import BaseLLM
from brain.logging_setup import get_logger

from agents.cancellation import CancellationToken
from agents.tools import REGISTRY, ask as ask_tools, dispatch
from agents.tools.memory import bind_memory, unbind_memory

logger = get_logger(__name__)

DEFAULT_MAX_TURNS = 6
DEFAULT_COMPACTION_TOKENS = 24000

_ZERO_USAGE = {"prompt": 0, "completion": 0, "total": 0}

_COMPACT_SENTINEL = "Compaction summary of earlier conversation"
_COMPACT_PROMPT = (
    "I am compressing a tool-working conversation into concise notes for a "
    "continuation. Preserve: the original task, decisions made, tool results "
    "already obtained, key findings, and what still remains to do. "
    "Output only the notes, no preamble.\n\nConversation so far:\n"
)

_JSON_TYPE = {str: "string", int: "integer", float: "number", bool: "boolean"}


def openai_tool_schemas() -> List[Dict[str, Any]]:
    """Build OpenAI function-tool schemas from the tool registry."""
    schemas: List[Dict[str, Any]] = []
    for name, entry in REGISTRY.items():
        properties: Dict[str, Any] = {}
        required: List[str] = []
        optional = set(entry.get("optional", ()))
        for pname, ptype in entry["parameters"].items():
            properties[pname] = {"type": _JSON_TYPE.get(ptype, "string"), "description": pname}
            if pname not in optional:
                required.append(pname)
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": entry["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return schemas


def _tool_call_dict(tool_call: Any) -> Dict[str, Any]:
    """Convert an OpenAI tool-call object into a plain dict safe for replay."""
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.function.name,
            "arguments": tool_call.function.arguments or "{}",
        },
    }


_MAX_RESULT_CHARS = 8000


class BaseAgent:
    """Agent with an LLM client, tools, and a supervised tool-calling loop."""

    def __init__(
        self,
        name: str,
        llm: BaseLLM,
        approve: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        memory: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> None:
        self.name = name
        self.llm = llm
        self.approve = approve
        self.max_turns = max_turns
        self.memory = memory
        self.tools = tools or openai_tool_schemas()
        self.cancel_token = cancel_token
        self.history: List[Dict[str, Any]] = []
        self.usage_accum: Dict[str, int] = dict(_ZERO_USAGE)
        self.on_generate: Optional[Callable[[str], None]] = None
        self.on_tool: Optional[Callable[[str, str], None]] = None
        self.compaction_threshold = int(os.getenv("PICO_COMPACTION_TOKENS", DEFAULT_COMPACTION_TOKENS))
        self._run_tokens = 0
        self._compacted_once = False

    def system_instructions(self) -> str:
        """Return this agent's system prompt."""
        return ""

    def _generate(self, messages: List[Dict[str, Any]]) -> Any:
        """Call the LLM, accumulate token usage, and emit the generate hook."""
        response = self.llm.generate(messages=messages)
        last = getattr(self.llm, "last_generation", _ZERO_USAGE)
        for key in self.usage_accum:
            self.usage_accum[key] += last.get(key, 0)
        if self.on_generate is not None:
            try:
                content = self._text_of(response)
            except Exception:  # noqa: BLE001 - content extraction must never break a run
                content = ""
            self.on_generate(self.name, content)
        return response

    async def _generate_async(self, messages: List[Dict[str, Any]]) -> Any:
        """Call a synchronous LLM client without blocking the UI event loop."""
        response = await asyncio.to_thread(self.llm.generate, messages=messages)
        last = getattr(self.llm, "last_generation", _ZERO_USAGE)
        for key in self.usage_accum:
            self.usage_accum[key] += last.get(key, 0)
        if self.on_generate is not None:
            try:
                content = self._text_of(response)
            except Exception:  # noqa: BLE001 - content extraction must never break a run
                content = ""
            self.on_generate(self.name, content)
        return response

    @property
    def usage(self) -> Dict[str, int]:
        """Token usage accumulated on this agent's own generations."""
        return dict(self.usage_accum)

    def run(self, task: str) -> str:
        """Run the tool-calling loop for a task and return the final text."""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()
        system_prompt = self.system_instructions()
        if self.memory is not None:
            context = self.memory.load_context() or ""
            if context:
                system_prompt = f"{system_prompt}\n\n## Notes about the master\n{context}"
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})
        self.history = messages

        bind_memory(self.memory)
        self._run_tokens = 0
        self._compacted_once = False
        try:
            final_text = self._loop(task, messages)
        finally:
            unbind_memory()
        self._capture_after_task(task, final_text)
        return final_text

    async def run_async(self, task: str) -> str:
        """Run the asynchronous tool loop used by event-driven interfaces."""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()
        system_prompt = self.system_instructions()
        if self.memory is not None:
            context = self.memory.load_context() or ""
            if context:
                system_prompt = f"{system_prompt}\n\n## Notes about the master\n{context}"
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})
        self.history = messages

        bind_memory(self.memory)
        self._run_tokens = 0
        self._compacted_once = False
        try:
            final_text = await self._loop_async(messages)
        finally:
            unbind_memory()
        self._capture_after_task(task, final_text)
        return final_text

    async def _loop_async(self, messages: List[Dict[str, Any]]) -> str:
        """Run the asynchronous supervised tool-calling flow for event-driven UIs.

        Mirrors :meth:`_loop` but awaits each LLM turn and each tool call, so the
        UI event loop stays reactive: LLM calls run in worker threads and approval
        callbacks may be coroutines (e.g. Textual modal screens).
        """
        if not self.llm.tools:
            self.llm.tools = self.tools

        for turn in range(self.max_turns):
            if self.cancel_token is not None:
                self.cancel_token.raise_if_cancelled()
            message = await self._ask_turn_async(messages)
            if message is None:
                break
            tool_calls = self._tool_calls_of(message)
            self._append_assistant(messages, message.content or "", tool_calls)
            if not tool_calls:
                logger.info(f"[bold green]{self.name} finished[/bold green] after {turn + 1} turn(s)")
                return message.content or ""
            await self._run_tool_calls_async(messages, tool_calls)
        return f"{self.name}: I could not finish the task within {self.max_turns} turns."

    def _loop(self, task: str, messages: List[Dict[str, Any]]) -> str:
        """Run the common supervised tool-calling flow; return the final answer.

        Every turn follows the same pipeline: ask the LLM, detect any tool
        calls, extract their name and arguments, execute them (under
        supervision), shape each result into a compact JSON tool message, and
        append the assistant + tool turns back to ``history``. The loop repeats
        until the LLM answers without a tool call, and the answer is returned
        to the caller.
        """
        if not self.llm.tools:
            self.llm.tools = self.tools

        for turn in range(self.max_turns):
            if self.cancel_token is not None:
                self.cancel_token.raise_if_cancelled()
            message = self._ask_turn(messages)
            if message is None:
                break
            tool_calls = self._tool_calls_of(message)
            self._append_assistant(messages, message.content or "", tool_calls)
            if not tool_calls:
                logger.info(f"[bold green]{self.name} finished[/bold green] after {turn + 1} turn(s)")
                return message.content or ""
            self._run_tool_calls(messages, tool_calls)
        return f"{self.name}: I could not finish the task within {self.max_turns} turns."

    def _ask_turn(self, messages: List[Dict[str, Any]]) -> Any:
        """Run one LLM turn and return its chat message (None on no response)."""
        response = self._generate(messages)
        self._maybe_compact(messages)
        return self._message_of(response)

    async def _ask_turn_async(self, messages: List[Dict[str, Any]]) -> Any:
        """Run one asynchronous LLM turn and return its chat message."""
        response = await self._generate_async(messages)
        await self._maybe_compact_async(messages)
        return self._message_of(response)

    def _tool_calls_of(self, message: Any) -> List[Any]:
        """Detect the tool calls on an LLM chat message."""
        return list(getattr(message, "tool_calls", None) or [])

    def _append_assistant(
        self,
        messages: List[Dict[str, Any]],
        content: str,
        tool_calls: List[Any],
    ) -> None:
        """Append the assistant turn (with any tool calls) to the history."""
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": [_tool_call_dict(tc) for tc in tool_calls],
            }
        )

    def _run_tool_calls(self, messages: List[Dict[str, Any]], tool_calls: List[Any]) -> None:
        """Execute every tool call and append a compact result message per call."""
        for tool_call in tool_calls:
            name = tool_call.function.name
            args = self._parse_args(tool_call)
            result = self._execute(name, args)
            self._stream_tool_output(name, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": self._process_result(result),
                }
            )

    async def _run_tool_calls_async(self, messages: List[Dict[str, Any]], tool_calls: List[Any]) -> None:
        """Execute every tool call (off-thread) and append result messages."""
        for tool_call in tool_calls:
            name = tool_call.function.name
            args = self._parse_args(tool_call)
            result = await self._execute_async(name, args)
            self._stream_tool_output(name, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": self._process_result(result),
                }
            )

    def _stream_tool_output(self, name: str, result: Dict[str, Any]) -> None:
        """Stream a short, readable snippet of a tool result to the UI hook.

        Tool results are shown live (e.g. gmail output, news headlines) even
        when the LLM only emits tool calls and no text. The hook is optional
        and must never break the run.
        """
        if self.on_tool is None:
            return
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
        text = (text or "").strip()
        if len(text) > 500:
            text = text[:500] + "…"
        if not text:
            return
        try:
            self.on_tool(self.name, f"[{name}] {text}")
        except Exception:  # noqa: BLE001 - a UI hook must not break the tool loop
            logger.debug("tool output hook failed: %s", name, exc_info=True)

    def _execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Approve then run one tool call; a failing call never raises."""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()
        if self.approve is not None and not self.approve(name, args):
            logger.warning(f"[bold yellow]{self.name} tool rejected[/bold yellow]: {name}")
            return {"ok": False, "error": "rejected by the master"}
        try:
            return dispatch(name, **args)
        except Exception as exc:  # noqa: BLE001 - a failing tool must not kill the loop
            logger.error(f"[bold red]Tool crashed[/bold red]: {name} -> {exc}")
            return {"ok": False, "error": f"tool {name} crashed: {exc}"}

    async def _execute_async(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Async approve-then-run one tool call, mirroring :meth:`_execute`.

        The approval callback may return a coroutine (e.g. an async Textual modal
        prompt); the actual tool runs in a worker thread so blocking tools never
        stall the UI event loop. A failing call never raises.
        """
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()
        if self.approve is not None:
            decision = self.approve(name, args)
            if inspect.isawaitable(decision):
                decision = await decision
            if not decision:
                logger.warning(f"[bold yellow]{self.name} tool rejected[/bold yellow]: {name}")
                return {"ok": False, "error": "rejected by the master"}
        try:
            return await asyncio.to_thread(dispatch, name, **args)
        except Exception as exc:  # noqa: BLE001 - a failing tool must not kill the loop
            logger.error(f"[bold red]Tool crashed[/bold red]: {name} -> {exc}")
            return {"ok": False, "error": f"tool {name} crashed: {exc}"}

    def _parse_args(self, tool_call: Any) -> Dict[str, Any]:
        """Parse a tool-call's JSON arguments, tolerating fences and stray noise."""
        raw = getattr(getattr(tool_call, "function", None), "arguments", None)
        cleaned = (raw or "{}").strip()
        cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
        if not cleaned:
            return {}
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                return {}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return data if isinstance(data, dict) else {}

    def _process_result(self, result: Any) -> str:
        """Serialize a tool result into a compact, JSON-safe tool message body."""
        def _clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: _clean(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_clean(v) for v in value]
            if isinstance(value, str):
                return value if len(value) <= _MAX_RESULT_CHARS else value[:_MAX_RESULT_CHARS] + "…"
            return value

        try:
            serialized = json.dumps(_clean(result), ensure_ascii=False)
        except (TypeError, ValueError):
            serialized = json.dumps(
                {"ok": False, "error": f"tool result is not JSON-serializable: {type(result).__name__}"}
            )
        if len(serialized) > _MAX_RESULT_CHARS * 2:
            serialized = serialized[:_MAX_RESULT_CHARS * 2] + "…"
        return serialized

    def _maybe_compact(self, messages: List[Dict[str, Any]]) -> None:
        """Auto-compact a long-running conversation into a summary context.

        Once the tokens spent on this task cross ``compaction_threshold`` the
        conversation is folded into a compact system summary (via one extra LLM
        call) and the loop continues from there. Runs at most once per task.
        """
        self._run_tokens += getattr(self.llm, "last_generation", _ZERO_USAGE).get("total", 0)
        if self._compacted_once or self._run_tokens <= self.compaction_threshold:
            return
        summary = self._compact_summary(messages)
        self._apply_compaction(messages, summary)

    async def _maybe_compact_async(self, messages: List[Dict[str, Any]]) -> None:
        """Async variant of :meth:`_maybe_compact` for event-driven UIs."""
        self._run_tokens += getattr(self.llm, "last_generation", _ZERO_USAGE).get("total", 0)
        if self._compacted_once or self._run_tokens <= self.compaction_threshold:
            return
        summary = await self._compact_summary_async(messages)
        self._apply_compaction(messages, summary)

    def _apply_compaction(self, messages: List[Dict[str, Any]], summary: str) -> None:
        """Fold the conversation history into a compact summary system prompt."""
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        compacted: List[Dict[str, Any]] = [
            {"role": "system", "content": f"{system}\n\n## {_COMPACT_SENTINEL}\n{summary}"}
        ]
        if messages and messages[-1].get("role") not in {"system", "tool"}:
            compacted.append(dict(messages[-1]))
        messages[:] = compacted
        self._compacted_once = True
        logger.info(f"[bold yellow]Conversation compacted[/bold yellow] after {self._run_tokens} tokens")

    def _compact_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Ask the LLM to compress past turns into concise continuation notes."""
        prompt = _COMPACT_PROMPT + self._condense_messages(messages)
        response = self._generate([{"role": "user", "content": prompt}])
        return self._text_of(response) or "(compaction notes unavailable)"

    async def _compact_summary_async(self, messages: List[Dict[str, Any]]) -> str:
        """Async variant of :meth:`_compact_summary` for event-driven UIs."""
        prompt = _COMPACT_PROMPT + self._condense_messages(messages)
        response = await self._generate_async([{"role": "user", "content": prompt}])
        return self._text_of(response) or "(compaction notes unavailable)"

    def _condense_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Render the latest messages as compact ``role: content`` lines."""
        condensed: List[str] = []
        for message in messages[-24:]:
            role = message.get("role", "?")
            content = message.get("content", "")
            if not isinstance(content, str):
                content = json.dumps({k: v for k, v in message.items() if k != "role"})
            if len(content) > 600:
                content = content[:600] + "…"
            condensed.append(f"{role}: {content}")
        return "\n".join(condensed)

    def _capture_after_task(self, task: str, final_text: str) -> None:
        """Persist working + episodic memory notes for a finished task."""
        if self.memory is None:
            return
        try:
            self.memory.note_now(
                key=f"task:{(task.strip() or 'untitled')[:60]}",
                value=final_text.strip()[:200],
            )
            self.memory.add_episode(
                title=task.strip()[:80] or "untitled task",
                summary=final_text.strip()[:300],
            )
        except Exception as exc:  # noqa: BLE001 - memory failures never break a task
            logger.warning(f"[bold yellow]Could not capture memory[/bold yellow]: {exc}")

    def _message_of(self, response: Any) -> Any:
        """Extract the chat message from a client response."""
        if hasattr(response, "choices"):
            return response.choices[0].message
        if hasattr(response, "content"):
            return response
        return None

    def _text_of(self, response: Any) -> str:
        """Extract plain text from a client response."""
        message = self._message_of(response)
        if message is None:
            return str(response)
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else str(content)