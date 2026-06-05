# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from brigid.config import (
    BashToolsConfig,
    PermissionsConfig,
    RuntimeConfig,
)
from brigid.permissions import PermissionGate
from brigid.session import ConversationSession
from brigid.tools import ToolRegistry
from brigid.tools.builtin import Bash

# ---- Fake LLM that yields scripted streamed parts ----


@dataclass
class _FakeMessage:
    content: str = ""
    thinking: str = ""
    tool_calls: list[Any] | None = None


@dataclass
class _FakePart:
    message: _FakeMessage
    done: bool = False


@dataclass
class _FakeFn:
    name: str
    arguments: dict[str, Any]


@dataclass
class _FakeToolCall:
    function: _FakeFn


class _Script:
    """Yield a list of pre-built parts. Then the next call yields the next list."""

    def __init__(self, scripts: list[list[_FakePart]]) -> None:
        self.scripts = list(scripts)
        self.calls = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[_FakePart]:
        idx = self.calls
        self.calls += 1
        if idx >= len(self.scripts):
            raise AssertionError(f"FakeLLM ran out of scripted responses (call #{idx})")
        for part in self.scripts[idx]:
            yield part


def _say(*chunks: str, tool_calls: list[_FakeToolCall] | None = None) -> list[_FakePart]:
    """Build a single response made of N text chunks; tool_calls (if any) attach to the final part."""
    parts: list[_FakePart] = []
    for i, c in enumerate(chunks):
        is_last = i == len(chunks) - 1
        msg = _FakeMessage(content=c, tool_calls=tool_calls if is_last else None)
        parts.append(_FakePart(message=msg, done=is_last))
    if not chunks:  # pure tool-call response
        parts.append(_FakePart(message=_FakeMessage(tool_calls=tool_calls), done=True))
    return parts


@pytest.mark.asyncio
async def test_simple_response_no_tools():
    llm = _Script([_say("hello", " world")])
    session = ConversationSession(
        llm,
        ToolRegistry.empty(),
        PermissionGate(PermissionsConfig()),
        RuntimeConfig(),
    )
    session.add_user("hi")
    await session.run_turn()
    assert session.messages[-1] == {"role": "assistant", "content": "hello world"}


@pytest.mark.asyncio
async def test_tool_call_round_trip():
    # First response: model calls bash. Second response: model says ok.
    call = _FakeToolCall(function=_FakeFn(name="bash", arguments={"command": "echo hi"}))
    llm = _Script(
        [
            _say(tool_calls=[call]),
            _say("done"),
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(Bash(BashToolsConfig(timeout_seconds=5)))
    gate = PermissionGate(PermissionsConfig(allow=["bash:*"]))
    session = ConversationSession(llm, registry, gate, RuntimeConfig())
    session.add_user("run echo")
    await session.run_turn()

    roles = [m["role"] for m in session.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    tool_msg = session.messages[2]
    assert tool_msg["tool_name"] == "bash"
    assert "exit_code: 0" in tool_msg["content"]
    assert "hi" in tool_msg["content"]
    assert session.messages[-1]["content"] == "done"


@pytest.mark.asyncio
async def test_denied_tool_call_returns_canned_string():
    call = _FakeToolCall(function=_FakeFn(name="bash", arguments={"command": "rm -rf /"}))
    llm = _Script(
        [
            _say(tool_calls=[call]),
            _say("understood"),
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(Bash(BashToolsConfig()))
    gate = PermissionGate(PermissionsConfig(deny=["bash:rm *"]))
    session = ConversationSession(llm, registry, gate, RuntimeConfig())
    session.add_user("nuke it")
    await session.run_turn()
    tool_msg = session.messages[2]
    assert tool_msg == {"role": "tool", "content": "[denied by policy]", "tool_name": "bash"}


@pytest.mark.asyncio
async def test_unknown_tool_call_returns_helpful_error():
    call = _FakeToolCall(function=_FakeFn(name="not_a_tool", arguments={}))
    llm = _Script(
        [
            _say(tool_calls=[call]),
            _say("oh well"),
        ]
    )
    session = ConversationSession(
        llm, ToolRegistry.empty(), PermissionGate(PermissionsConfig()), RuntimeConfig()
    )
    session.add_user("call something fake")
    await session.run_turn()
    assert "unknown tool" in session.messages[2]["content"]


@pytest.mark.asyncio
async def test_max_steps_per_turn_enforced():
    # Loop forever asking for the same tool. Cap should fire after N steps.
    call = _FakeToolCall(function=_FakeFn(name="bash", arguments={"command": "echo hi"}))
    # Provide an unbounded supply of tool-calling responses
    scripts = [_say(tool_calls=[call]) for _ in range(10)]
    llm = _Script(scripts)
    registry = ToolRegistry.empty()
    registry.register(Bash(BashToolsConfig(timeout_seconds=5)))
    gate = PermissionGate(PermissionsConfig(allow=["bash:*"]))

    errors: list[BaseException] = []

    class CaptureRenderer:
        def on_assistant_chunk(self, content: str) -> None:
            pass

        def on_thinking_chunk(self, content: str) -> None:
            pass

        def on_assistant_done(self) -> None:
            pass

        def on_tool_call(self, name: str, args: dict) -> None:
            pass

        def on_tool_result(self, name: str, result: str, *, denied: bool) -> None:
            pass

        def on_error(self, err: BaseException) -> None:
            errors.append(err)

        def on_busy(self, label: str) -> None:
            pass

        def on_idle(self) -> None:
            pass

    session = ConversationSession(
        llm, registry, gate, RuntimeConfig(max_steps_per_turn=3), renderer=CaptureRenderer()
    )
    session.add_user("loop")
    await session.run_turn()
    assert len(errors) == 1
    assert "step limit" in str(errors[0])


@pytest.mark.asyncio
async def test_thinking_chunks_collected():
    # Ollama qwen3 emits .thinking on parts. Verify we collect it.
    parts = [
        _FakePart(message=_FakeMessage(thinking="thinking…")),
        _FakePart(message=_FakeMessage(content="answer"), done=True),
    ]
    llm = _Script([parts])
    session = ConversationSession(
        llm, ToolRegistry.empty(), PermissionGate(PermissionsConfig()), RuntimeConfig()
    )
    session.add_user("q")
    await session.run_turn()
    assistant = session.messages[-1]
    assert assistant["content"] == "answer"
    assert assistant["thinking"] == "thinking…"


# ---- busy/idle event tests --------------------------------------------------


class _EventRecorder:
    """Renderer that records every event as a tuple in order, for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def on_assistant_chunk(self, content: str) -> None:
        self.events.append(("chunk", content))

    def on_thinking_chunk(self, content: str) -> None:
        self.events.append(("thinking", content))

    def on_assistant_done(self) -> None:
        self.events.append(("done", None))

    def on_tool_call(self, name: str, args: dict) -> None:
        self.events.append(("tool_call", name))

    def on_tool_result(self, name: str, result: str, *, denied: bool) -> None:
        self.events.append(("tool_result", (name, denied)))

    def on_error(self, err: BaseException) -> None:
        self.events.append(("error", str(err)))

    def on_busy(self, label: str) -> None:
        self.events.append(("busy", label))

    def on_idle(self) -> None:
        self.events.append(("idle", None))


@pytest.mark.asyncio
async def test_busy_idle_sequence_around_tool_call():
    """Verify the busy/idle event sequence across an LLM-call-then-tool-call-then-LLM-call turn."""
    call = _FakeToolCall(function=_FakeFn(name="bash", arguments={"command": "echo hi"}))
    llm = _Script(
        [
            _say(tool_calls=[call]),  # first round: model wants to call bash
            _say("done"),  # second round: model wraps up
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(Bash(BashToolsConfig(timeout_seconds=5)))
    gate = PermissionGate(PermissionsConfig(allow=["bash:*"]))
    rec = _EventRecorder()
    session = ConversationSession(llm, registry, gate, RuntimeConfig(), renderer=rec)
    session.add_user("go")
    await session.run_turn()

    kinds = [e[0] for e in rec.events]
    # Round 1 is tool-only (no chunks): busy → idle (from finally) → done → tool_call
    #   → busy("running bash") → idle (after tool.run) → tool_result.
    # Round 2 has a chunk: busy → idle (on first chunk) → chunk → done.
    assert kinds == [
        "busy",
        "idle",
        "done",
        "tool_call",
        "busy",
        "idle",
        "tool_result",
        "busy",
        "idle",
        "chunk",
        "done",
    ]
    # Spot-check the labels.
    busy_labels = [e[1] for e in rec.events if e[0] == "busy"]
    assert busy_labels[0] == "waiting for model"
    assert busy_labels[1] == "running bash"
    assert busy_labels[2] == "waiting for model"
    # Every busy is matched by an idle, in pairs.
    busy_count = kinds.count("busy")
    idle_count = kinds.count("idle")
    assert busy_count == idle_count == 3


class _FailingLLM:
    """LLM whose stream raises after yielding nothing — simulates network blowup
    or cancellation before the first chunk arrives."""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        raise RuntimeError("boom")
        yield  # unreachable; makes this an async generator


@pytest.mark.asyncio
async def test_idle_emitted_even_when_stream_raises():
    rec = _EventRecorder()
    session = ConversationSession(
        _FailingLLM(),
        ToolRegistry.empty(),
        PermissionGate(PermissionsConfig()),
        RuntimeConfig(),
        renderer=rec,
    )
    session.add_user("x")
    with pytest.raises(RuntimeError, match="boom"):
        await session.run_turn()
    kinds = [e[0] for e in rec.events]
    # Must have both a busy and a matching idle, in that order.
    assert "busy" in kinds
    assert "idle" in kinds
    assert kinds.index("busy") < kinds.index("idle")
