# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brigid.tools.mcp_bridge import MCPToolAdapter, _flatten_mcp_result


@dataclass
class _FakeMCPTool:
    name: str
    description: str | None
    inputSchema: dict[str, Any] | None


@dataclass
class _FakeTextBlock:
    text: str


@dataclass
class _FakeImageBlock:
    data: bytes


@dataclass
class _FakeMCPResult:
    content: list[Any]
    isError: bool = False


def test_adapter_namespaces_tool_name():
    fake = _FakeMCPTool(
        name="read_file",
        description="Read a file",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    adapter = MCPToolAdapter("filesystem", fake, session=None)  # type: ignore[arg-type]
    assert adapter.name == "mcp.filesystem.read_file"
    assert adapter.description == "Read a file"
    assert adapter.parameters_schema == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }


def test_adapter_falls_back_to_empty_schema_when_missing():
    fake = _FakeMCPTool(name="ping", description=None, inputSchema=None)
    adapter = MCPToolAdapter("svc", fake, session=None)  # type: ignore[arg-type]
    assert adapter.parameters_schema == {"type": "object", "properties": {}}
    assert "ping" in adapter.description


def test_adapter_to_ollama_schema_round_trip():
    fake = _FakeMCPTool(
        name="add",
        description="add two numbers",
        inputSchema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    adapter = MCPToolAdapter("math", fake, session=None)  # type: ignore[arg-type]
    schema = adapter.to_ollama_schema()
    assert schema == {
        "type": "function",
        "function": {
            "name": "mcp.math.add",
            "description": "add two numbers",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        },
    }


def test_permission_key_is_stable_for_same_args():
    fake = _FakeMCPTool(name="x", description="x", inputSchema={"type": "object"})
    adapter = MCPToolAdapter("svc", fake, session=None)  # type: ignore[arg-type]
    k1 = adapter.permission_key({"a": 1, "b": 2})
    k2 = adapter.permission_key({"b": 2, "a": 1})
    assert k1 == k2


def test_flatten_mcp_result_text_blocks():
    result = _FakeMCPResult(content=[_FakeTextBlock("line1"), _FakeTextBlock("line2")])
    assert _flatten_mcp_result(result) == "line1\nline2"


def test_flatten_mcp_result_non_text_block_placeholder():
    result = _FakeMCPResult(content=[_FakeImageBlock(b"\x89PNG")])
    out = _flatten_mcp_result(result)
    assert "_FakeImageBlock" in out


def test_flatten_mcp_result_error_flag():
    result = _FakeMCPResult(content=[_FakeTextBlock("boom")], isError=True)
    assert _flatten_mcp_result(result).startswith("[mcp error]")


def test_flatten_mcp_result_empty():
    result = _FakeMCPResult(content=[])
    assert _flatten_mcp_result(result) == "(empty mcp result)"
