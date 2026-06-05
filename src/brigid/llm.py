# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ollama import AsyncClient

from brigid.config import OllamaConfig


class OllamaBackend:
    """Thin async wrapper over the ollama Python client.

    The interesting logic (turning streamed parts into a finished message,
    routing tool calls, etc.) lives in `session.py`. This class only handles
    transport: prepending the system prompt and forwarding to `chat`.
    """

    def __init__(self, cfg: OllamaConfig, client: AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or AsyncClient(host=cfg.host)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        """Stream chat parts from the model. Yields the raw ollama parts so the
        caller can inspect `.message.content`, `.message.thinking`, and on the
        final part `.message.tool_calls` plus `.done`."""
        prepared = self._with_system_prompt(messages)
        stream = await self.client.chat(
            model=self.cfg.model,
            messages=prepared,
            tools=tools or None,
            stream=True,
            options=self.cfg.options or None,
        )
        async for part in stream:
            yield part

    def _with_system_prompt(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sp = self.cfg.system_prompt
        leading_is_system = bool(messages) and messages[0].get("role") == "system"
        if not sp:
            # Cleared: respect any pre-existing system message (e.g. from /load).
            return messages
        if leading_is_system:
            # Replace the head so /system updates take effect on the next turn.
            return [{"role": "system", "content": sp}, *messages[1:]]
        return [{"role": "system", "content": sp}, *messages]
