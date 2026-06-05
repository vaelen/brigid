# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from brigid.config import RuntimeConfig
from brigid.errors import BrigidError
from brigid.permissions import PermissionGate
from brigid.tools import ToolRegistry


class LLMBackend(Protocol):
    """Async-generator-style protocol: calling `stream(...)` returns an
    AsyncIterator directly (the implementation is `async def` with `yield`)."""

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]: ...


class Renderer(Protocol):
    def on_assistant_chunk(self, content: str) -> None: ...
    def on_thinking_chunk(self, content: str) -> None: ...
    def on_assistant_done(self) -> None: ...
    def on_tool_call(self, name: str, args: dict[str, Any]) -> None: ...
    def on_tool_result(self, name: str, result: str, *, denied: bool) -> None: ...
    def on_error(self, err: BaseException) -> None: ...
    def on_busy(self, label: str) -> None: ...
    def on_idle(self) -> None: ...


class NullRenderer:
    """Renderer that swallows all events. Useful for tests and headless runs."""

    def on_assistant_chunk(self, content: str) -> None:
        pass

    def on_thinking_chunk(self, content: str) -> None:
        pass

    def on_assistant_done(self) -> None:
        pass

    def on_tool_call(self, name: str, args: dict[str, Any]) -> None:
        pass

    def on_tool_result(self, name: str, result: str, *, denied: bool) -> None:
        pass

    def on_error(self, err: BaseException) -> None:
        pass

    def on_busy(self, label: str) -> None:
        pass

    def on_idle(self) -> None:
        pass


@dataclass
class _StreamCollector:
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from obj whether it's an attribute or a mapping entry."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_chunk(part: Any, attr: str) -> str:
    msg = _get(part, "message")
    if msg is None:
        return ""
    return _get(msg, attr, "") or ""


def _extract_tool_calls(part: Any) -> list[dict[str, Any]]:
    msg = _get(part, "message")
    if msg is None:
        return []
    raw = _get(msg, "tool_calls") or []
    out: list[dict[str, Any]] = []
    for tc in raw:
        fn = _get(tc, "function", {}) or {}
        name = _get(fn, "name", "") or ""
        args = _get(fn, "arguments", {}) or {}
        out.append({"function": {"name": name, "arguments": dict(args)}})
    return out


class ConversationSession:
    """Holds the conversation state and runs the agent loop."""

    def __init__(
        self,
        llm: LLMBackend,
        registry: ToolRegistry,
        gate: PermissionGate,
        runtime: RuntimeConfig,
        renderer: Renderer | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.gate = gate
        self.runtime = runtime
        self.renderer: Renderer = renderer or NullRenderer()
        self.messages: list[dict[str, Any]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def clear(self) -> None:
        self.messages.clear()

    async def run_turn(self) -> None:
        """Drive the inner agent loop until the model returns a message with no
        tool calls (or we hit the per-turn step limit)."""
        for _step in range(self.runtime.max_steps_per_turn):
            assistant = await self._stream_once()
            self.messages.append(assistant)
            if not assistant.get("tool_calls"):
                return
            for call in assistant["tool_calls"]:
                result = await self._run_tool_call(call)
                self.messages.append(result)
        self.renderer.on_error(
            BrigidError(f"per-turn step limit ({self.runtime.max_steps_per_turn}) exceeded")
        )

    async def _stream_once(self) -> dict[str, Any]:
        collector = _StreamCollector()
        schemas = self.registry.ollama_schemas() or None
        self.renderer.on_busy("waiting for model")
        idled = False
        try:
            async for part in self.llm.stream(self.messages, schemas):
                content = _extract_chunk(part, "content")
                thinking = _extract_chunk(part, "thinking")
                if (content or thinking) and not idled:
                    self.renderer.on_idle()
                    idled = True
                if content:
                    collector.content += content
                    self.renderer.on_assistant_chunk(content)
                if thinking:
                    collector.thinking += thinking
                    self.renderer.on_thinking_chunk(thinking)
                # tool_calls only appear on the final part — overwrite each time
                tcs = _extract_tool_calls(part)
                if tcs:
                    collector.tool_calls = tcs
        finally:
            # Pair the on_busy() above with exactly one on_idle, even if the stream
            # produced no chunks (tool-only response) or raised mid-flight.
            if not idled:
                self.renderer.on_idle()
        self.renderer.on_assistant_done()
        msg: dict[str, Any] = {"role": "assistant", "content": collector.content}
        if collector.thinking:
            msg["thinking"] = collector.thinking
        if collector.tool_calls:
            msg["tool_calls"] = collector.tool_calls
        return msg

    async def _run_tool_call(self, call: dict[str, Any]) -> dict[str, Any]:
        name = call["function"]["name"]
        args = call["function"]["arguments"]
        self.renderer.on_tool_call(name, args)
        tool = self.registry.get(name)
        if tool is None:
            err = f"unknown tool: {name!r}"
            self.renderer.on_tool_result(name, err, denied=False)
            return _tool_message(name, err)
        try:
            key = tool.permission_key(args)
        except Exception as e:
            err = f"failed to derive permission key: {type(e).__name__}: {e}"
            self.renderer.on_tool_result(name, err, denied=False)
            return _tool_message(name, err)
        allowed = await self.gate.check(key)
        if not allowed:
            self.renderer.on_tool_result(name, "[denied by policy]", denied=True)
            return _tool_message(name, "[denied by policy]")
        self.renderer.on_busy(f"running {name}")
        try:
            result = await tool.run(**args)
        except Exception as e:
            result = f"tool error: {type(e).__name__}: {e}"
        finally:
            self.renderer.on_idle()
        self.renderer.on_tool_result(name, result, denied=False)
        return _tool_message(name, result)


def _tool_message(name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "content": content, "tool_name": name}


# A small alias used by tests.
ToolPrompter = Callable[[str], Awaitable[bool]]
