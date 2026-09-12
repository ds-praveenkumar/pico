"""Shared base for all pico agents.

``BaseAgent`` owns an LLM client, a tool set, and a supervised tool-calling loop.
Subclasses override :meth:`system_instructions` for their role. Every tool call
goes through an optional supervisor (``approve``) before it executes.
"""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from brain.base_llm import BaseLLM
from brain.logging_setup import get_logger

from agents.tools import REGISTRY, dispatch
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
        for pname, ptype in entry["parameters"].items():
            properties[pname] = {"type": _JSON_TYPE.get(ptype, "string"), "description": pname}
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
        approve: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        memory: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.name = name
        self.llm = llm
        self.approve = approve
        self.max_turns = max_turns
        self.memory = memory
        self.tools = tools or openai_tool_schemas()
        self.history: List[Dict[str, Any]] = []
        self.usage_accum: Dict[str, int] = dict(_ZERO_USAGE)
        self.on_generate: Optional[Callable[[str], None]] = None
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

    @property
    def usage(self) -> Dict[str, int]:
        """Token usage accumulated on this agent's own generations."""
        return dict(self.usage_accum)

    def run(self, task: str) -> str:
        """Run the tool-calling loop for a task and return the final text."""
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

    def _loop(self, task: str, messages: List[Dict[str, Any]]) -> str:
        """Run the supervised tool-calling loop; returns the final answer text.

        Each turn: ask the LLM, detect any tool calls, parse their arguments,
        run the tools (under supervision), process the results into compact
        JSON tool messages, append them back to ``messages``, and loop until
        the LLM answers without a tool call.
        """
        if not self.llm.tools:
            self.llm.tools = self.tools

        for turn in range(self.max_turns):
            response = self._generate(messages)
            self._maybe_compact(messages)
            message = self._message_of(response)
            if response is None or message is None:
                break
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [_tool_call_dict(tc) for tc in tool_calls],
                }
            )
            if not tool_calls:
                logger.info(f"[bold green]{self.name} finished[/bold green] after {turn + 1} turn(s)")
                return message.content or ""

            for tc in tool_calls:
                name = tc.function.name
                args: Dict[str, Any] = self._parse_args(tc)
                if self.approve is not None and not self.approve(name, args):
                    logger.warning(f"[bold yellow]{self.name} tool rejected[/bold yellow]: {name}")
                    result: Dict[str, Any] = {"ok": False, "error": "rejected by the master"}
                else:
                    try:
                        result = dispatch(name, **args)
                    except Exception as exc:  # noqa: BLE001 - a failing tool must not kill the loop
                        logger.error(f"[bold red]Tool crashed[/bold red]: {name} -> {exc}")
                        result = {"ok": False, "error": f"tool {name} crashed: {exc}"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": self._process_result(result),
                    }
                )
        return f"{self.name}: I could not finish the task within {self.max_turns} turns."

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
        condensed: List[str] = []
        for message in messages[-24:]:
            role = message.get("role", "?")
            content = message.get("content", "")
            if not isinstance(content, str):
                content = json.dumps({k: v for k, v in message.items() if k != "role"})
            if len(content) > 600:
                content = content[:600] + "…"
            condensed.append(f"{role}: {content}")
        prompt = _COMPACT_PROMPT + "\n".join(condensed)
        response = self._generate([{"role": "user", "content": prompt}])
        return self._text_of(response) or "(compaction notes unavailable)"

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