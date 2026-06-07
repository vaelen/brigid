# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from typing import Any

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from brigid.permissions import PromptOutcome

_RESULT_PREVIEW_BYTES = 1_500


class RichRenderer:
    """rich-based Renderer that prints assistant tokens live, panels for tool
    calls, and dim-italic for the model's `thinking` field."""

    def __init__(
        self,
        console: Console | None = None,
        show_thinking: bool = False,
        assistant_label: str = "brigid",
    ) -> None:
        self.console = console or Console()
        self.show_thinking = show_thinking
        self.assistant_label = assistant_label
        self._in_assistant = False
        self._in_thinking = False
        self._status: Status | None = None

    def on_assistant_chunk(self, content: str) -> None:
        if self._in_thinking:
            self.console.print()
            self._in_thinking = False
        if not self._in_assistant:
            self.console.print(Text(f"{self.assistant_label}: ", style="bold green"), end="")
            self._in_assistant = True
        # Print as plain text — disable rich markup so model output isn't reinterpreted.
        self.console.print(content, end="", highlight=False, markup=False)

    def on_thinking_chunk(self, content: str) -> None:
        if not self.show_thinking:
            return
        if not self._in_thinking:
            self.console.print(Text("(thinking) ", style="dim italic"), end="")
            self._in_thinking = True
        self.console.print(content, end="", style="dim italic", highlight=False, markup=False)

    def on_assistant_done(self) -> None:
        if self._in_assistant or self._in_thinking:
            self.console.print()
        self._in_assistant = False
        self._in_thinking = False

    def on_tool_call(self, name: str, args: dict[str, Any]) -> None:
        body = f"{name}({_short_args(args)})"
        self.console.print(f"[cyan]▶[/cyan] {escape(body)}")

    def on_tool_result(self, name: str, result: str, *, denied: bool) -> None:
        if denied:
            self.console.print(f"[red]✕[/red] {escape(name)}: denied by policy")
            return
        snippet = result
        if len(snippet) > _RESULT_PREVIEW_BYTES:
            snippet = (
                snippet[:_RESULT_PREVIEW_BYTES]
                + f"\n… [+{len(result) - _RESULT_PREVIEW_BYTES} more bytes]"
            )
        self.console.print(
            Panel(
                escape(snippet) or "(no output)",
                title=f"← {name}",
                border_style="dim",
                expand=False,
            )
        )

    def on_error(self, err: BaseException) -> None:
        self.console.print(f"[red]error: {escape(str(err))}[/red]")

    def on_busy(self, label: str) -> None:
        text = f"[dim]{escape(label)}…[/dim]"
        if self._status is not None:
            self._status.update(text)
            return
        self._status = self.console.status(text, spinner="dots")
        self._status.__enter__()

    def on_idle(self) -> None:
        if self._status is None:
            return
        self._status.__exit__(None, None, None)
        self._status = None


def _short_args(args: dict[str, Any], max_len: int = 120) -> str:
    try:
        s = json.dumps(args, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def make_permission_prompter(
    console: Console,
    psession: PromptSession,
):
    """Return an async prompter satisfying `permissions.Prompter`."""

    async def prompter(key: str) -> PromptOutcome:
        console.print(
            Panel(
                f"[bold]{escape(key)}[/bold]",
                title="permission required",
                border_style="yellow",
                expand=False,
            )
        )
        console.print(
            "  [bold]y[/bold] allow once  ·  [bold]Y[/bold] allow always  ·  "
            "[bold]n[/bold] deny once  ·  [bold]N[/bold] deny always  ·  "
            "[bold]e[/bold] edit pattern then allow always"
        )
        while True:
            try:
                choice = (await psession.prompt_async("permission> ")).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("[dim](permission denied — input cancelled)[/dim]")
                return PromptOutcome(allow=False)
            if choice == "y":
                return PromptOutcome(allow=True)
            if choice == "n":
                return PromptOutcome(allow=False)
            if choice == "Y":
                pat = await _ask_pattern(psession, key)
                return PromptOutcome(allow=True, persist=("allow", pat))
            if choice == "N":
                pat = await _ask_pattern(psession, key)
                return PromptOutcome(allow=False, persist=("deny", pat))
            if choice == "e":
                pat = await _ask_pattern(psession, key)
                return PromptOutcome(allow=True, persist=("allow", pat))
            console.print("[dim]invalid choice — enter y / Y / n / N / e[/dim]")

    return prompter


async def _ask_pattern(psession: PromptSession, default_key: str) -> str:
    text = await psession.prompt_async("pattern> ", default=default_key)
    return text.strip() or default_key
