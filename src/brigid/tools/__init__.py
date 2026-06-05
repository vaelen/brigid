# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from brigid.errors import BrigidError


class Tool(ABC):
    """A callable the model can invoke. Subclasses provide a JSON-schema
    parameter spec, a permission-key derivation, and an async run().

    `name`, `description`, and `parameters_schema` may be set either as class
    attributes (for built-in tools) or as instance attributes in `__init__`
    (e.g. for MCP-tool adapters that learn their schema at connection time)."""

    name: str
    description: str
    parameters_schema: dict[str, Any]

    def permission_key(self, args: dict[str, Any]) -> str:
        """Default key is just the tool name; subclasses should override to
        embed argument values that callers want to allow/deny against."""
        return self.name

    @abstractmethod
    async def run(self, **args: Any) -> str:
        """Execute the tool. Return a string the model will see as the tool result."""
        ...

    def to_ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


@dataclass
class ToolRegistry:
    tools: dict[str, Tool]

    @classmethod
    def empty(cls) -> ToolRegistry:
        return cls(tools={})

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise BrigidError(f"duplicate tool name: {tool.name!r}")
        self.tools[tool.name] = tool

    def register_all(self, tools: Sequence[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self.tools.values())

    def __len__(self) -> int:
        return len(self.tools)

    def ollama_schemas(self) -> list[dict[str, Any]]:
        return [t.to_ollama_schema() for t in self.tools.values()]
