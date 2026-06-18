# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import tomli_w
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory

from brigid.config import Config, OllamaConfig
from brigid.errors import BrigidError, MCPConnectionError
from brigid.llm import OllamaBackend
from brigid.permissions import PermissionGate
from brigid.references import AtFileCompleter, expand_references
from brigid.render import RichRenderer, make_permission_prompter
from brigid.session import ConversationSession
from brigid.tools import ToolRegistry
from brigid.tools.builtin import builtin_tools
from brigid.tools.mcp_bridge import MCPManager

HISTORY_PATH = Path.home() / ".local" / "state" / "brigid" / "history"


class _RendererProto(Protocol):
    """Structural type for what _handle_slash needs from a renderer."""

    console: Any
    show_thinking: bool
    assistant_label: str

    def on_error(self, err: BaseException) -> None: ...


@dataclass
class _ActiveModel:
    name: str
    cfg: OllamaConfig
    personality: str | None = None


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

        active_name, active_cfg = cfg.active()
        active = _ActiveModel(active_name, active_cfg)
        _apply_startup_personality(cfg, active, console)
        llm = OllamaBackend(active.cfg)
        session = ConversationSession(llm, registry, gate, cfg.runtime, renderer=renderer)

        _print_banner(console, active, registry)

        fs_root = Path(cfg.tools.fs.root)
        completer = AtFileCompleter(fs_root)

        while True:
            try:
                line = await psession.prompt_async(
                    FormattedText([("class:prompt", "you> ")]),
                    multiline=True,
                    completer=completer,
                    complete_while_typing=True,
                )
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("/"):
                if not await _handle_slash(stripped, cfg, active, session, registry, renderer):
                    break
                continue

            expanded = await expand_references(line, fs_root, gate)
            if expanded is None:
                console.print("[dim]@-reference denied — turn skipped[/dim]")
                continue
            renderer.assistant_label = active.personality or "brigid"
            session.add_user(expanded)
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
    active: _ActiveModel,
    session: ConversationSession | None,
    registry: ToolRegistry | None,
    renderer: _RendererProto,
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
        assert registry is not None
        for tool in registry:
            short = tool.description.splitlines()[0] if tool.description else ""
            console.print(f"  [bold]{tool.name}[/bold] — {short}")
        return True
    if cmd == "/clear":
        assert session is not None
        session.clear()
        console.print("[dim]conversation cleared[/dim]")
        return True
    if cmd == "/model":
        if not arg:
            _print_models(console, cfg, active)
        else:
            name = arg.strip()
            resolved = cfg.resolve(name)
            if resolved is None:
                avail = ", ".join(cfg.profile_names()) or "(none defined)"
                console.print(f"unknown model: [bold]{name}[/bold] — available: {avail}")
            else:
                active.name = name
                active.cfg.model = resolved.model
                active.cfg.host = resolved.host
                active.cfg.options = resolved.options
                active.cfg.system_prompt = resolved.system_prompt
                active.cfg.tools = resolved.tools
                if active.personality is not None:
                    body = cfg.load_personality(active.personality)
                    if body is None:
                        active.personality = None  # file gone — drop the stale marker
                    else:
                        active.cfg.system_prompt = body
                ctx = resolved.options.get("num_ctx")
                ctx_note = f", num_ctx={ctx}" if ctx is not None else ""
                host_note = f", host={resolved.host}" if resolved.host != cfg.brigid.host else ""
                tools_note = "" if resolved.tools else ", tools off"
                console.print(
                    f"model set to [bold]{name}[/bold] "
                    f"([dim]{resolved.model}{ctx_note}{host_note}{tools_note}[/dim])"
                )
        return True
    if cmd == "/system":
        if not arg:
            current = active.cfg.system_prompt or "(none)"
            console.print(f"current system prompt:\n[dim]{current}[/dim]")
        elif arg.strip() == "clear":
            active.cfg.system_prompt = None
            active.personality = None
            console.print("[dim]system prompt cleared[/dim]")
        else:
            active.cfg.system_prompt = arg
            active.personality = None
            console.print(f"[dim]system prompt set ({len(arg)} chars)[/dim]")
        return True
    if cmd == "/personality":
        if not arg:
            current = active.personality or "(none)"
            console.print(f"active personality: [bold]{current}[/bold]")
            available = cfg.list_personalities()
            listing = ", ".join(available) if available else "(none found)"
            console.print(f"[dim]available: {listing}[/dim]")
            return True
        name = arg.strip()
        if name == "none":
            active.cfg.system_prompt = None
            active.personality = None
            console.print("[dim]personality cleared[/dim]")
            return True
        body = cfg.load_personality(name)
        if body is None:
            available = ", ".join(cfg.list_personalities()) or "(none found)"
            console.print(f"unknown personality: [bold]{name}[/bold] — available: {available}")
            return True
        active.cfg.system_prompt = body
        active.personality = name
        console.print(f"[dim]personality set to [bold]{name}[/bold] ({len(body)} chars)[/dim]")
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
        assert session is not None
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
        assert session is not None
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
  /model [name]         list models or switch to a defined profile
  /system [text|clear]  show, set, or clear the system prompt
  /personality [name]   load a personality; "none" to clear; no arg lists
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


def _apply_startup_personality(cfg: Config, active: _ActiveModel, console) -> None:
    name = cfg.brigid.personality
    if name is None:
        return
    body = cfg.load_personality(name)
    if body is None:
        console.print(
            f"[yellow]personality {name!r} not found in "
            f"{cfg.personalities_dir()}; continuing without it[/yellow]"
        )
        return
    active.cfg.system_prompt = body
    active.personality = name
    console.print(f"[dim]personality: [bold]{name}[/bold][/dim]")


def _print_models(console, cfg: Config, active: _ActiveModel) -> None:
    names = cfg.profile_names()
    if not names:
        console.print("[dim]no models defined in config[/dim]")
        return
    width = max(len(n) for n in names)
    for n in names:
        prof = cfg.models[n]
        ctx = prof.options.get("num_ctx", "—")
        marker = "[green]●[/green]" if n == active.name else " "
        tools_note = "" if prof.tools else "  [dim](no tools)[/dim]"
        console.print(
            f"  {marker} [bold]{n:<{width}}[/bold]  {prof.model}  (num_ctx={ctx}){tools_note}"
        )
    console.print(f"[dim]active: {active.name}[/dim]")


def _print_banner(console, active: _ActiveModel, registry: ToolRegistry) -> None:
    console.print(
        f"[bold]brigid[/bold] · model=[bold]{active.name}[/bold] "
        f"([dim]{active.cfg.model}[/dim]) · host={active.cfg.host} · "
        f"{len(registry)} tool(s) loaded"
    )
    console.print(
        "[dim]/help for commands · Alt+Enter to send, Enter for newline · Ctrl-D to quit[/dim]"
    )


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
