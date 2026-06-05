# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

"""End-to-end smoke test: drive a real Ollama call through the agent loop.

Run with:  uv run python scripts/smoke.py

Tests:
    1. Plain "hello" — verify streaming + clean text response.
    2. Tool call — ask the model to use bash to compute something.
"""

from __future__ import annotations

import asyncio
import sys

from brigid.config import (
    BashToolsConfig,
    FsToolsConfig,
    OllamaConfig,
    PermissionsConfig,
    RuntimeConfig,
    WebToolsConfig,
)
from brigid.llm import OllamaBackend
from brigid.permissions import PermissionGate
from brigid.session import ConversationSession
from brigid.tools import ToolRegistry
from brigid.tools.builtin import builtin_tools


class StdoutRenderer:
    """Minimal renderer that writes plainly to stdout so this script works in
    any terminal (including the Bash tool). No rich, no markup."""

    def __init__(self) -> None:
        self._wrote = False

    def on_assistant_chunk(self, content: str) -> None:
        if not self._wrote:
            sys.stdout.write("\n[assistant] ")
            self._wrote = True
        sys.stdout.write(content)
        sys.stdout.flush()

    def on_thinking_chunk(self, content: str) -> None:
        pass  # silenced

    def on_assistant_done(self) -> None:
        if self._wrote:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._wrote = False

    def on_tool_call(self, name: str, args: dict) -> None:
        sys.stdout.write(f"\n[tool→ {name}({args})]\n")
        sys.stdout.flush()

    def on_tool_result(self, name: str, result: str, *, denied: bool) -> None:
        marker = "DENIED" if denied else "ok"
        snippet = result if len(result) <= 400 else result[:400] + "…"
        sys.stdout.write(f"[tool← {name} {marker}]\n{snippet}\n")
        sys.stdout.flush()

    def on_error(self, err: BaseException) -> None:
        sys.stdout.write(f"\n[error] {err}\n")
        sys.stdout.flush()


async def main() -> int:
    import os

    model = os.environ.get("BRIGID_SMOKE_MODEL", "qwen3.6:35b-a3b")
    print(f"using model: {model}")
    cfg_ollama = OllamaConfig(
        model=model,
        host="http://localhost:11434",
        # num_gpu=0 forces CPU-only to dodge a Metal kernel compile failure in
        # ollama 0.21.2 on this macOS build. Slower, but verifies harness wiring.
        options={"temperature": 0.2, "num_ctx": 4096, "num_gpu": 0},
    )
    registry = ToolRegistry.empty()
    registry.register_all(
        builtin_tools(
            FsToolsConfig(root="."), BashToolsConfig(timeout_seconds=30), WebToolsConfig()
        )
    )
    gate = PermissionGate(PermissionsConfig(allow=["bash:*", "fs.*", "web.fetch:*"]))
    runtime = RuntimeConfig(max_steps_per_turn=10)
    renderer = StdoutRenderer()
    llm = OllamaBackend(cfg_ollama)

    print("=" * 60)
    print("TEST 1: plain hello")
    print("=" * 60)
    s1 = ConversationSession(llm, registry, gate, runtime, renderer=renderer)
    s1.add_user("Reply with exactly the words: hello from brigid.")
    await s1.run_turn()

    print()
    print("=" * 60)
    print("TEST 2: tool call (bash)")
    print("=" * 60)
    s2 = ConversationSession(llm, registry, gate, runtime, renderer=renderer)
    s2.add_user("Use the bash tool to run `echo brigid-tool-test` and then report what it printed.")
    await s2.run_turn()

    print()
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
