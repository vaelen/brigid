# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

import tomli_w
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory

from brigid.config import Config
from brigid.errors import BrigidError, MCPConnectionError
from brigid.llm import OllamaBackend
from brigid.permissions import PermissionGate
from brigid.render import RichRenderer, make_permission_prompter
from brigid.session import ConversationSession
from brigid.tools import ToolRegistry
from brigid.tools.builtin import builtin_tools
from brigid.tools.mcp_bridge import MCPManager

HISTORY_PATH = Path.home() / ".local" / "state" / "brigid" / "history"


async def run(cfg: Config) -> int:
    """Top-level REPL entry point."""
    renderer = RichRenderer()
    console = renderer.console
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    psession: PromptSession[str] = PromptSession(history=FileHistory(str(HISTORY_PATH)))

    prompter = make_permission_prompter(console, psession)
    gate = PermissionGate(cfg.permissions, prompter)

    registry = ToolRegistry.empty()
    registry.register_all(builtin_tools(cfg.tools.fs, cfg.tools.bash, cfg.tools.web))

    async with AsyncExitStack() as stack:
        mcp: MCPManager | None = None
        if cfg.mcp_servers:
            try:
                mcp = await stack.enter_async_context(MCPManager(cfg.mcp_servers))
                registry.register_all(mcp.tools)
                console.print(
                    f"[dim]connected to {len(cfg.mcp_servers)} MCP server(s); "
                    f"{len(mcp.tools)} tool(s) attached[/dim]"
                )
            except MCPConnectionError as e:
                renderer.on_error(e)
                console.print("[dim]continuing without MCP tools[/dim]")

        llm = OllamaBackend(cfg.ollama)
        session = ConversationSession(llm, registry, gate, cfg.runtime, renderer=renderer)

        _print_banner(console, cfg, registry)

        while True:
            try:
                line = await psession.prompt_async(FormattedText([("class:prompt", "you> ")]))
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("/"):
                if not await _handle_slash(stripped, cfg, session, registry, renderer):
                    break
                continue

            session.add_user(line)
            turn_task = asyncio.create_task(session.run_turn())
            try:
                await turn_task
            except KeyboardInterrupt:
                turn_task.cancel()
                with _suppress_cancel():
                    await turn_task
                console.print("[dim](turn cancelled)[/dim]")
            except asyncio.CancelledError:
                console.print("[dim](turn cancelled)[/dim]")
            except Exception as e:
                renderer.on_error(e)

    if cfg.runtime.persist_permissions:
        _persist_permissions(cfg, console)

    return 0


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


async def _handle_slash(
    line: str,
    cfg: Config,
    session: ConversationSession,
    registry: ToolRegistry,
    renderer: RichRenderer,
) -> bool:
    """Return False to exit the REPL, True to keep going."""
    parts = line.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    console = renderer.console

    if cmd in ("/exit", "/quit"):
        return False
    if cmd == "/help":
        console.print(_HELP_TEXT)
        return True
    if cmd == "/tools":
        for tool in registry:
            short = tool.description.splitlines()[0] if tool.description else ""
            console.print(f"  [bold]{tool.name}[/bold] — {short}")
        return True
    if cmd == "/clear":
        session.clear()
        console.print("[dim]conversation cleared[/dim]")
        return True
    if cmd == "/model":
        if not arg:
            console.print(f"current model: [bold]{cfg.ollama.model}[/bold]")
        else:
            cfg.ollama.model = arg.strip()
            console.print(f"model set to [bold]{cfg.ollama.model}[/bold]")
        return True
    if cmd == "/system":
        if not arg:
            current = cfg.ollama.system_prompt or "(none)"
            console.print(f"current system prompt:\n[dim]{current}[/dim]")
        elif arg.strip() == "clear":
            cfg.ollama.system_prompt = None
            console.print("[dim]system prompt cleared[/dim]")
        else:
            cfg.ollama.system_prompt = arg
            console.print(f"[dim]system prompt set ({len(arg)} chars)[/dim]")
        return True
    if cmd == "/allow":
        if not arg:
            console.print("usage: /allow <pattern>")
        else:
            cfg.permissions.allow.append(arg.strip())
            console.print(f"[green]+ allow[/green] {arg.strip()}")
        return True
    if cmd == "/deny":
        if not arg:
            console.print("usage: /deny <pattern>")
        else:
            cfg.permissions.deny.append(arg.strip())
            console.print(f"[red]+ deny[/red] {arg.strip()}")
        return True
    if cmd == "/thinking":
        if arg.strip() == "on":
            renderer.show_thinking = True
        elif arg.strip() == "off":
            renderer.show_thinking = False
        console.print(f"thinking display: {'on' if renderer.show_thinking else 'off'}")
        return True
    if cmd == "/save":
        if not arg:
            console.print("usage: /save <path>")
            return True
        try:
            Path(arg).write_text(json.dumps(session.messages, indent=2, default=str))
            console.print(f"saved {len(session.messages)} messages to {arg}")
        except OSError as e:
            renderer.on_error(BrigidError(f"save failed: {e}"))
        return True
    if cmd == "/load":
        if not arg:
            console.print("usage: /load <path>")
            return True
        try:
            data = json.loads(Path(arg).read_text())
            if not isinstance(data, list):
                raise BrigidError("session file must contain a JSON list of messages")
            session.messages = list(data)
            console.print(f"loaded {len(session.messages)} messages from {arg}")
        except (OSError, json.JSONDecodeError, BrigidError) as e:
            renderer.on_error(BrigidError(f"load failed: {e}"))
        return True

    console.print(f"unknown command: {cmd} — try /help")
    return True


_HELP_TEXT = """\
[bold]slash commands[/bold]
  /help                 show this help
  /tools                list registered tools
  /model [name]         show or switch active model
  /system [text|clear]  show, set, or clear the system prompt
  /clear                wipe conversation history
  /save <path>          save session to a JSON file
  /load <path>          load session from a JSON file
  /allow <pattern>      add a permission allow pattern
  /deny <pattern>       add a permission deny pattern
  /thinking on|off      show or hide model thinking
  /exit                 quit
press [bold]Ctrl-C[/bold] to cancel an in-flight turn; [bold]Ctrl-D[/bold] to quit.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_banner(console, cfg: Config, registry: ToolRegistry) -> None:
    console.print(
        f"[bold]brigid[/bold] · model=[bold]{cfg.ollama.model}[/bold] · "
        f"host={cfg.ollama.host} · {len(registry)} tool(s) loaded"
    )
    console.print("[dim]/help for commands · Ctrl-D to quit[/dim]")


class _suppress_cancel:
    """Context manager that swallows asyncio.CancelledError."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is asyncio.CancelledError


def _persist_permissions(cfg: Config, console) -> None:
    """Write current allow/deny lists back to the source TOML file (if known)."""
    if cfg.source_path is None:
        return
    path: Path = cfg.source_path
    raw: dict
    if path.exists():
        try:
            import tomllib

            with path.open("rb") as f:
                raw = tomllib.load(f)
        except Exception:
            return
    else:
        raw = {}
    raw.setdefault("permissions", {})
    raw["permissions"]["allow"] = list(cfg.permissions.allow)
    raw["permissions"]["deny"] = list(cfg.permissions.deny)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            tomli_w.dump(raw, f)
    except OSError as e:
        console.print(f"[dim]could not persist permissions: {e}[/dim]")
