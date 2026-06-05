# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from brigid.config import MCPServerConfig
from brigid.errors import MCPConnectionError
from brigid.tools import Tool


class MCPToolAdapter(Tool):
    """Wraps an MCP-server tool so it looks like a local Tool to the registry.

    Tool name is namespaced as `mcp.<server>.<tool>`. The MCP `inputSchema` is
    forwarded directly as the parameters schema (it's already JSON Schema)."""

    def __init__(self, server_name: str, mcp_tool: Any, session: ClientSession) -> None:
        self.name = f"mcp.{server_name}.{mcp_tool.name}"
        self.description = mcp_tool.description or f"{server_name} tool: {mcp_tool.name}"
        self.parameters_schema = (
            dict(mcp_tool.inputSchema)
            if getattr(mcp_tool, "inputSchema", None)
            else {"type": "object", "properties": {}}
        )
        self._mcp_tool_name = mcp_tool.name
        self._session = session

    def permission_key(self, args: dict[str, Any]) -> str:
        try:
            args_repr = json.dumps(args, sort_keys=True, default=str)
        except (TypeError, ValueError):
            args_repr = str(args)
        return f"{self.name}:{args_repr}"

    async def run(self, **args: Any) -> str:
        result = await self._session.call_tool(self._mcp_tool_name, arguments=args)
        return _flatten_mcp_result(result)


def _flatten_mcp_result(result: Any) -> str:
    """Concatenate text blocks; for non-text blocks emit a placeholder."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            kind = type(block).__name__
            parts.append(f"[{kind} (not rendered)]")
    body = "\n".join(parts)
    if getattr(result, "isError", False):
        return f"[mcp error]\n{body}" if body else "[mcp error]"
    return body or "(empty mcp result)"


class MCPManager:
    """Manages connections to one or more MCP stdio servers and exposes their
    tools as `Tool` adapters. Use as an async context manager."""

    def __init__(self, server_configs: list[MCPServerConfig]) -> None:
        self.server_configs = server_configs
        self._stack: AsyncExitStack | None = None
        self.sessions: dict[str, ClientSession] = {}
        self.tools: list[MCPToolAdapter] = []

    async def __aenter__(self) -> MCPManager:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for cfg in self.server_configs:
            try:
                await self._connect(cfg)
            except Exception as e:
                # Clean up anything already opened, then bubble up.
                await self._stack.__aexit__(type(e), e, e.__traceback__)
                self._stack = None
                raise MCPConnectionError(
                    f"failed to connect to MCP server {cfg.name!r}: {e}"
                ) from e
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, tb)
            self._stack = None

    async def _connect(self, cfg: MCPServerConfig) -> None:
        assert self._stack is not None
        merged_env = {**os.environ, **cfg.env} if cfg.env else None
        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=merged_env,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listing = await session.list_tools()
        for t in listing.tools:
            adapter = MCPToolAdapter(cfg.name, t, session)
            self.tools.append(adapter)
        self.sessions[cfg.name] = session
