# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Brigid is a local agentic chat harness: a terminal REPL that drives an Ollama model through a tool-use loop, with MCP server support, built-in tools (filesystem, shell, web fetch), and a permission gate on every tool call. Entry point is `brigid.cli:main` (exposed as the `brigid` script).

## Commands

```bash
uv sync                  # install env + deps
uv run brigid            # run the REPL (loads ~/.config/brigid/config.toml)
uv run brigid -c path    # run with an explicit config file

uv run ruff format       # format
uv run ruff check        # lint (line length handled by formatter, E501 ignored)
uv run pyright           # type-check (standard mode, src + tests)
uv run pytest            # full test suite

uv run pytest tests/test_session.py                       # single file
uv run pytest tests/test_session.py::test_name            # single test
uv run python scripts/smoke.py                            # end-to-end against a live Ollama
```

`scripts/smoke.py` requires a running Ollama with the model pulled; override the model via `BRIGID_SMOKE_MODEL`. The rest of the suite is hermetic (no Ollama needed) — `asyncio_mode = "auto"`, so async tests need no decorator.

## Architecture

The agent loop is deliberately split so the model transport stays dumb and the orchestration is testable in isolation.

- **`session.py` — the agent loop.** `ConversationSession.run_turn()` repeatedly streams one assistant message, appends it, and if it contains `tool_calls`, runs each through the permission gate and feeds results back, looping until the model returns a message with no tool calls (or `max_steps_per_turn` is hit). This is where streamed parts are assembled into a finished message and tool calls are routed — **not** in `llm.py`. The model backend is injected as the `LLMBackend` Protocol and the UI as the `Renderer` Protocol, so the loop runs headless in tests with `NullRenderer` and a fake backend.

- **`llm.py` — transport only.** `OllamaBackend.stream()` prepends the system prompt and forwards to `ollama.AsyncClient.chat(stream=True)`, yielding raw ollama parts. The system prompt is injected per-stream (replacing any leading system message), which is why `/system` changes take effect on the next turn. Ollama parts are read via `_get()`/`_extract_*` helpers in `session.py` that tolerate both attribute and dict access.

- **`permissions.py` — the gate.** `evaluate()` is a pure function: deny patterns first, then allow, else PROMPT. `PermissionGate.check()` runs an interactive prompter on PROMPT and can persist newly-chosen patterns into the in-memory allow/deny lists. Patterns are `fnmatch` globs matched against a per-tool **permission key**.

- **`tools/__init__.py` — the `Tool` ABC + `ToolRegistry`.** Every tool defines `name`, `description`, `parameters_schema` (JSON Schema), an async `run(**args) -> str`, and `permission_key(args)`. The permission key is what the gate matches: built-ins embed argument values (e.g. `bash:<command>`, `fs.write:<resolved-path>`, `web.fetch:<url>`) so policies can target specific operations. `ollama_schemas()` converts the registry to the tool-spec list passed to the model each turn.

- **`tools/builtin.py` — fs.read / fs.write / fs.edit / fs.list / bash / web.fetch.** Filesystem tools confine paths to `tools.fs.root` via `_resolve()` (raises `ToolError` on traversal; root `"/"` disables confinement). Permission keys use the *resolved* path.

- **`tools/mcp_bridge.py` — MCP integration.** `MCPManager` is an async context manager that spawns configured stdio MCP servers and wraps each remote tool as an `MCPToolAdapter` (a normal `Tool`). Adapter names are namespaced `mcp.<server>.<tool>`; permission keys are `mcp.<server>.<tool>:<json-args>` (args JSON-sorted so the key is stable regardless of arg order). MCP connection failure is non-fatal — the REPL warns and continues without those tools.

- **`config.py` — typed TOML config.** Frozen dataclasses built from a TOML dict via `from_dict`. `${env:VAR}` interpolation is applied recursively across all string values before building. A missing config file yields all-defaults (not an error). `source_path` is retained so permissions can be written back.

- **`repl.py` — wiring + slash commands.** Builds renderer → prompter → gate → registry (built-ins, then MCP tools) → backend → session, then loops reading input. `/`-prefixed lines are slash commands; everything else is a user turn. Ctrl-C cancels the in-flight turn; Ctrl-D quits. On exit, if `runtime.persist_permissions`, the current allow/deny lists are merged back into the source TOML (preserving other keys).

- **`render.py` — `RichRenderer` + permission prompter.** Implements the `Renderer` Protocol with rich. Model output is printed with `markup=False`/`highlight=False` so it is never reinterpreted as rich markup. The interactive prompter returns a `PromptOutcome` (allow + optional persist target), keeping I/O out of the permission core.

## Conventions

- All modules start with `from __future__ import annotations`; target is Python 3.11.
- Errors derive from `BrigidError` in `errors.py`; tools raise `ToolError`, which `session.py` catches and returns to the model as a `tool error: ...` string rather than crashing the turn.
- Renderer and LLM seams are Protocols, not base classes — prefer adding a new implementation over threading conditionals through the loop.
- A tool result is always a string the model will see; surface failures in that string, don't swallow them.
