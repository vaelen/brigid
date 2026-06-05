# Model Profiles + Upgraded `/model` Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Brigid's single `[ollama]` model block with named `[models.<name>]` profiles (each carrying its own model id, options/context, system prompt, and optional host), and upgrade `/model` to switch between whole profiles.

**Architecture:** `config.py` gains `BrigidConfig` (system-wide `default` + `host`) and `ModelProfile`, and `Config` resolves a profile into the existing mutable `OllamaConfig` that the backend reads. `repl.py` holds the active model in a small `_ActiveModel` holder that `/model` mutates in place. `llm.py` rebuilds its `AsyncClient` when the active host changes.

**Tech Stack:** Python 3.11, frozen dataclasses, `tomllib`, pytest (`asyncio_mode = "auto"`), `rich`.

**Design spec:** `docs/superpowers/specs/2026-06-06-model-profiles-design.md`

---

## File Structure

- `src/brigid/config.py` — **modify**: add `BrigidConfig`, `ModelProfile`; drop the `ollama` field from `Config`; add `brigid` + `models` fields; add `resolve()`, `active()`, `profile_names()`; remove `_build_ollama`, add `_build_brigid`/`_build_models`; validate `default` in `from_dict`. `OllamaConfig` itself stays (now the runtime active-model struct).
- `src/brigid/llm.py` — **modify**: rebuild `AsyncClient` when `cfg.host` changes.
- `src/brigid/repl.py` — **modify**: `_ActiveModel` holder, wire `cfg.active()`, upgrade `/model`, repoint `/system` + banner at the active model.
- `tests/test_config.py` — **modify**: rewrite the two `[ollama]`-based tests; add profile/resolution tests.
- `tests/test_llm.py` — **modify**: add client-rebuild tests.
- `tests/test_repl.py` — **create**: `/model` list / switch / unknown-name behavior.

---

## Task 1: Config — model profiles, resolution, validation

**Files:**
- Modify: `src/brigid/config.py`
- Test: `tests/test_config.py` (full rewrite of the file)

- [ ] **Step 1: Replace `tests/test_config.py` with the new suite (failing)**

Write this exact content to `tests/test_config.py`:

```python
# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import textwrap

import pytest

from brigid.config import (
    Config,
    MCPServerConfig,
    OllamaConfig,
    from_dict,
    load,
)
from brigid.errors import ConfigError


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert isinstance(cfg, Config)
    assert cfg.models == {}
    assert cfg.brigid.default is None
    name, active = cfg.active()
    assert name == "default"
    assert active.model == OllamaConfig.model
    assert cfg.runtime.max_steps_per_turn == 25
    assert cfg.permissions.allow == []
    assert cfg.mcp_servers == []
    assert cfg.source_path == tmp_path / "missing.toml"


def test_load_valid_file(tmp_path):
    body = textwrap.dedent("""
        [brigid]
        default = "hermes"
        host    = "http://example:11434"

        [runtime]
        max_steps_per_turn = 10

        [models.hermes]
        model         = "hermes3-8b"
        system_prompt = "You are Hermes."
        options       = { num_ctx = 32768, temperature = 0.7 }

        [models.stheno]
        model   = "stheno-8b"
        options = { num_ctx = 8192 }

        [permissions]
        allow = ["fs.read:*"]
        deny  = ["bash:rm -rf *"]

        [[mcp.servers]]
        name = "fs"
        command = "npx"
        args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    """)
    path = tmp_path / "c.toml"
    path.write_text(body)
    cfg = load(path)
    assert cfg.brigid.default == "hermes"
    assert cfg.brigid.host == "http://example:11434"
    assert list(cfg.models) == ["hermes", "stheno"]
    name, active = cfg.active()
    assert name == "hermes"
    assert active.model == "hermes3-8b"
    assert active.host == "http://example:11434"
    assert active.options == {"num_ctx": 32768, "temperature": 0.7}
    assert active.system_prompt == "You are Hermes."
    assert cfg.runtime.max_steps_per_turn == 10
    assert cfg.permissions.allow == ["fs.read:*"]
    assert cfg.mcp_servers == [
        MCPServerConfig(
            name="fs",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={},
        )
    ]


def test_model_defaults_to_profile_name():
    cfg = from_dict({"models": {"qwen": {"options": {"num_ctx": 4096}}}})
    assert cfg.models["qwen"].model == "qwen"
    resolved = cfg.resolve("qwen")
    assert resolved is not None
    assert resolved.model == "qwen"
    assert resolved.options == {"num_ctx": 4096}


def test_per_model_host_override_else_brigid_host():
    cfg = from_dict(
        {
            "brigid": {"host": "http://base:11434"},
            "models": {
                "a": {"model": "a"},
                "b": {"model": "b", "host": "http://other:11434"},
            },
        }
    )
    assert cfg.resolve("a").host == "http://base:11434"
    assert cfg.resolve("b").host == "http://other:11434"


def test_active_prefers_default_then_first_then_builtin():
    cfg = from_dict(
        {"brigid": {"default": "b"}, "models": {"a": {"model": "a"}, "b": {"model": "b"}}}
    )
    assert cfg.active()[0] == "b"

    cfg2 = from_dict({"models": {"a": {"model": "a"}, "b": {"model": "b"}}})
    assert cfg2.active()[0] == "a"

    cfg3 = from_dict({})
    name, active = cfg3.active()
    assert name == "default"
    assert active.model == OllamaConfig.model


def test_unknown_default_raises():
    with pytest.raises(ConfigError):
        from_dict({"brigid": {"default": "ghost"}, "models": {"a": {"model": "a"}}})


def test_resolve_unknown_returns_none():
    cfg = from_dict({"models": {"a": {"model": "a"}}})
    assert cfg.resolve("nope") is None


def test_env_substitution(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    body = textwrap.dedent("""
        [[mcp.servers]]
        name = "gh"
        command = "uvx"
        args = ["mcp-server-github"]
        env = { GITHUB_TOKEN = "${env:MY_TOKEN}" }
    """)
    path = tmp_path / "c.toml"
    path.write_text(body)
    cfg = load(path)
    assert cfg.mcp_servers[0].env == {"GITHUB_TOKEN": "s3cret"}


def test_env_substitution_missing_var_yields_empty(monkeypatch):
    monkeypatch.delenv("UNSET_FOR_TEST", raising=False)
    raw = {
        "mcp": {
            "servers": [{"name": "x", "command": "echo", "env": {"V": "${env:UNSET_FOR_TEST}"}}]
        }
    }
    cfg = from_dict(raw)
    assert cfg.mcp_servers[0].env == {"V": ""}


def test_invalid_mcp_server_raises():
    with pytest.raises(ConfigError):
        from_dict({"mcp": {"servers": [{"name": "no-cmd"}]}})


def test_malformed_toml_raises(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("not = valid = toml\n")
    with pytest.raises(ConfigError):
        load(path)
```

- [ ] **Step 2: Run the config tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL (e.g. `AttributeError: 'Config' object has no attribute 'active'` / `brigid`).

- [ ] **Step 3: Edit `src/brigid/config.py`**

(a) Add `ConfigError` is already imported. Add the two new dataclasses after `OllamaConfig` (leave `OllamaConfig` itself unchanged):

```python
@dataclass(frozen=True)
class BrigidConfig:
    default: str | None = None
    host: str = "http://localhost:11434"


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    system_prompt: str | None = None
    host: str | None = None
```

(b) Replace the `Config` dataclass (currently has the `ollama` field) with:

```python
@dataclass
class Config:
    brigid: BrigidConfig = field(default_factory=BrigidConfig)
    models: dict[str, ModelProfile] = field(default_factory=dict)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    source_path: Path | None = None

    def profile_names(self) -> list[str]:
        return list(self.models.keys())

    def resolve(self, name: str) -> OllamaConfig | None:
        prof = self.models.get(name)
        if prof is None:
            return None
        return OllamaConfig(
            model=prof.model,
            host=prof.host or self.brigid.host,
            options=dict(prof.options),
            system_prompt=prof.system_prompt,
        )

    def active(self) -> tuple[str, OllamaConfig]:
        name = self.brigid.default
        if name is None:
            name = next(iter(self.models), None)
        if name is None:
            return ("default", OllamaConfig())
        resolved = self.resolve(name)
        assert resolved is not None  # validated in from_dict
        return (name, resolved)
```

(c) Delete `_build_ollama` and add two builders:

```python
def _build_brigid(d: dict[str, Any]) -> BrigidConfig:
    return BrigidConfig(
        default=d.get("default"),
        host=d.get("host", BrigidConfig.host),
    )


def _build_models(d: dict[str, Any]) -> dict[str, ModelProfile]:
    out: dict[str, ModelProfile] = {}
    for name, entry in d.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"[models.{name}] must be a table")
        out[name] = ModelProfile(
            name=name,
            model=entry.get("model", name),
            options=dict(entry.get("options", {})),
            system_prompt=entry.get("system_prompt"),
            host=entry.get("host"),
        )
    return out
```

(d) Replace the body of `from_dict` with:

```python
def from_dict(raw: dict[str, Any]) -> Config:
    expanded = _expand_env_in(raw)
    mcp_block = expanded.get("mcp", {})
    brigid = _build_brigid(expanded.get("brigid", {}))
    models = _build_models(expanded.get("models", {}))
    if brigid.default is not None and brigid.default not in models:
        avail = ", ".join(models) or "(none)"
        raise ConfigError(
            f"[brigid].default = {brigid.default!r} is not a defined model; available: {avail}"
        )
    return Config(
        brigid=brigid,
        models=models,
        runtime=_build_runtime(expanded.get("runtime", {})),
        tools=_build_tools(expanded.get("tools", {})),
        permissions=_build_permissions(expanded.get("permissions", {})),
        mcp_servers=_build_mcp_servers(mcp_block.get("servers", [])),
    )
```

`load()` is unchanged (it already sets `cfg.source_path`).

- [ ] **Step 4: Run the config tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/brigid/config.py tests/test_config.py
git commit -m "feat(config): named model profiles with [brigid] + [models.*] and resolution"
```

---

## Task 2: LLM backend — rebuild client on host change

**Files:**
- Modify: `src/brigid/llm.py`
- Test: `tests/test_llm.py` (append two tests)

- [ ] **Step 1: Add failing tests to `tests/test_llm.py`**

Append to the end of `tests/test_llm.py`:

```python
def test_client_rebuilt_when_host_changes():
    cfg = OllamaConfig(host="http://a:11434")
    sentinel = object()
    b = OllamaBackend(cfg, client=sentinel)  # type: ignore[arg-type]
    assert b.client is sentinel
    cfg.host = "http://b:11434"
    b._ensure_client()
    assert b.client is not sentinel
    assert b._client_host == "http://b:11434"


def test_client_not_rebuilt_when_host_unchanged():
    cfg = OllamaConfig(host="http://a:11434")
    sentinel = object()
    b = OllamaBackend(cfg, client=sentinel)  # type: ignore[arg-type]
    b._ensure_client()
    assert b.client is sentinel
    assert b._client_host == "http://a:11434"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_llm.py -q`
Expected: FAIL with `AttributeError: 'OllamaBackend' object has no attribute '_ensure_client'`.

- [ ] **Step 3: Edit `src/brigid/llm.py`**

In `__init__`, record the host the client was built with:

```python
    def __init__(self, cfg: OllamaConfig, client: AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or AsyncClient(host=cfg.host)
        self._client_host = cfg.host
```

Add the `_ensure_client` method (place it just above `stream`):

```python
    def _ensure_client(self) -> None:
        """Rebuild the transport client if the active host changed (e.g. after /model)."""
        if self.cfg.host != self._client_host:
            self.client = AsyncClient(host=self.cfg.host)
            self._client_host = self.cfg.host
```

Call it at the top of `stream`, before building messages:

```python
    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        self._ensure_client()
        prepared = self._with_system_prompt(messages)
        ...
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_llm.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/brigid/llm.py tests/test_llm.py
git commit -m "feat(llm): rebuild AsyncClient when the active host changes"
```

---

## Task 3: REPL — active-model holder + upgraded `/model`

**Files:**
- Modify: `src/brigid/repl.py`
- Test: `tests/test_repl.py` (create)

- [ ] **Step 1: Create `tests/test_repl.py` (failing)**

Write this exact content to `tests/test_repl.py`:

```python
# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from brigid.config import from_dict
from brigid.repl import _ActiveModel, _handle_slash

RAW = {
    "brigid": {"default": "hermes"},
    "models": {
        "hermes": {
            "model": "hermes3-8b",
            "system_prompt": "You are Hermes.",
            "options": {"num_ctx": 32768},
        },
        "stheno": {"model": "stheno-8b", "options": {"num_ctx": 8192}},
    },
}


class _FakeConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))


class _FakeRenderer:
    def __init__(self) -> None:
        self.console = _FakeConsole()
        self.show_thinking = False


def _active(cfg):
    name, ocfg = cfg.active()
    return _ActiveModel(name, ocfg)


async def test_model_no_arg_lists_profiles():
    cfg = from_dict(RAW)
    active = _active(cfg)
    r = _FakeRenderer()
    cont = await _handle_slash("/model", cfg, active, None, None, r)
    assert cont is True
    out = "\n".join(r.console.lines)
    assert "hermes" in out
    assert "stheno" in out


async def test_model_switch_applies_all_fields():
    cfg = from_dict(RAW)
    active = _active(cfg)
    assert active.cfg.system_prompt == "You are Hermes."
    r = _FakeRenderer()
    await _handle_slash("/model stheno", cfg, active, None, None, r)
    assert active.name == "stheno"
    assert active.cfg.model == "stheno-8b"
    assert active.cfg.options["num_ctx"] == 8192
    assert active.cfg.system_prompt is None  # self-contained: cleared on switch


async def test_model_unknown_reports_available_and_no_change():
    cfg = from_dict(RAW)
    active = _active(cfg)
    before = (active.name, active.cfg.model)
    r = _FakeRenderer()
    await _handle_slash("/model nope", cfg, active, None, None, r)
    assert (active.name, active.cfg.model) == before
    out = "\n".join(r.console.lines)
    assert "hermes" in out
    assert "stheno" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_repl.py -q`
Expected: FAIL with `ImportError: cannot import name '_ActiveModel'`.

- [ ] **Step 3: Edit `src/brigid/repl.py`**

(a) Add to the imports near the top:

```python
from dataclasses import dataclass
```

and extend the config import:

```python
from brigid.config import Config, OllamaConfig
```

(b) Add the holder just below the imports (before `async def run`):

```python
@dataclass
class _ActiveModel:
    name: str
    cfg: OllamaConfig
```

(c) In `run()`, replace the backend-construction block:

```python
        llm = OllamaBackend(cfg.ollama)
        session = ConversationSession(llm, registry, gate, cfg.runtime, renderer=renderer)

        _print_banner(console, cfg, registry)
```

with:

```python
        active_name, active_cfg = cfg.active()
        active = _ActiveModel(active_name, active_cfg)
        llm = OllamaBackend(active.cfg)
        session = ConversationSession(llm, registry, gate, cfg.runtime, renderer=renderer)

        _print_banner(console, active, registry)
```

(d) In `run()`, update the slash dispatch call to pass `active`:

```python
                if not await _handle_slash(stripped, cfg, active, session, registry, renderer):
                    break
```

(e) Update the `_handle_slash` signature:

```python
async def _handle_slash(
    line: str,
    cfg: Config,
    active: _ActiveModel,
    session: ConversationSession,
    registry: ToolRegistry,
    renderer: RichRenderer,
) -> bool:
```

(f) Replace the entire `if cmd == "/model":` block with:

```python
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
                ctx = resolved.options.get("num_ctx")
                ctx_note = f", num_ctx={ctx}" if ctx is not None else ""
                host_note = (
                    f", host={resolved.host}" if resolved.host != cfg.brigid.host else ""
                )
                console.print(
                    f"model set to [bold]{name}[/bold] "
                    f"([dim]{resolved.model}{ctx_note}{host_note}[/dim])"
                )
        return True
```

(g) In the `if cmd == "/system":` block, repoint the three `cfg.ollama.system_prompt` references at `active.cfg.system_prompt`:

```python
    if cmd == "/system":
        if not arg:
            current = active.cfg.system_prompt or "(none)"
            console.print(f"current system prompt:\n[dim]{current}[/dim]")
        elif arg.strip() == "clear":
            active.cfg.system_prompt = None
            console.print("[dim]system prompt cleared[/dim]")
        else:
            active.cfg.system_prompt = arg
            console.print(f"[dim]system prompt set ({len(arg)} chars)[/dim]")
        return True
```

(h) Add the `_print_models` helper (place it next to `_print_banner`):

```python
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
        console.print(f"  {marker} [bold]{n:<{width}}[/bold]  {prof.model}  (num_ctx={ctx})")
    console.print(f"[dim]active: {active.name}[/dim]")
```

(i) Replace `_print_banner` with the active-aware version:

```python
def _print_banner(console, active: _ActiveModel, registry: ToolRegistry) -> None:
    console.print(
        f"[bold]brigid[/bold] · model=[bold]{active.name}[/bold] "
        f"([dim]{active.cfg.model}[/dim]) · host={active.cfg.host} · "
        f"{len(registry)} tool(s) loaded"
    )
    console.print("[dim]/help for commands · Ctrl-D to quit[/dim]")
```

(j) Update the `/model` line in `_HELP_TEXT`:

```python
  /model [name]         list models or switch to a defined profile
```

- [ ] **Step 4: Run the repl tests to verify pass**

Run: `uv run pytest tests/test_repl.py -q`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add src/brigid/repl.py tests/test_repl.py
git commit -m "feat(repl): profile-aware /model switching with active-model holder"
```

---

## Task 4: Full verification (lead-run, not a subagent)

This task touches the live Ollama install and the user's real config; the lead runs it interactively after Tasks 1–3 land.

- [ ] **Step 1: Full suite + lint + type-check**

Run:
```bash
uv run pytest -q
uv run ruff format
uv run ruff check
uv run pyright
```
Expected: all green. Fix any fallout (e.g. an unused import) before proceeding.

- [ ] **Step 2: Build the three Ollama models from the Modelfiles**

```bash
cd ~/.config/brigid/modelfiles
ollama create mag-mell-12b -f mag-mell-12b.Modelfile
ollama create stheno-8b    -f stheno-8b.Modelfile
ollama create hermes3-8b   -f hermes3-8b.Modelfile
ollama list | grep -E 'mag-mell-12b|stheno-8b|hermes3-8b'
```
Expected: each `create` succeeds; the three models appear in `ollama list`. (The Hermes tool template is the most likely to need a fix; if `create` errors on the template, adjust and re-run.)

- [ ] **Step 3: Migrate the user's `~/.config/brigid/config.toml` to the new schema**

Rewrite the `[ollama]` block to `[brigid]` + `[models.*]` (keeping `[runtime]`, `[tools.*]`, `[permissions]`):

```toml
[brigid]
default = "hermes"
host    = "http://localhost:11434"

[models.hermes]
model         = "hermes3-8b"
system_prompt = "You are Hermes, a capable, uncensored assistant. Use the provided tools when they help."
options       = { num_ctx = 32768, temperature = 0.7 }

[models.stheno]
model   = "stheno-8b"
options = { num_ctx = 8192, temperature = 1.1 }

[models.mag-mell]
model   = "mag-mell-12b"
options = { num_ctx = 16384, temperature = 1.25, min_p = 0.2 }
```

- [ ] **Step 4: Manual REPL smoke test**

Run `uv run brigid` and verify:
- Banner shows `model=hermes (hermes3-8b)`.
- `/model` lists hermes ●, stheno, mag-mell with their `num_ctx`.
- `/model stheno` confirms switch with `num_ctx=8192`; `/model mag-mell` shows `num_ctx=16384`.
- `/model bogus` prints `unknown model: bogus — available: hermes, stheno, mag-mell`.
- With hermes active, a prompt that needs a tool (e.g. "list the files in the current directory") triggers an `fs.list` tool call (validates the Hermes tool template end-to-end).

- [ ] **Step 5: Finalize**

Use the `superpowers:finishing-a-development-branch` skill to decide how to integrate `feature/model-profiles` (merge / PR / cleanup).

---

## Notes for the implementer

- `OllamaConfig` is intentionally **kept** and stays mutable — it is the runtime active-model struct the backend holds by reference. `/model` mutates the *fields* of `active.cfg` (never reassigns `active.cfg`), so the backend sees changes on the next turn.
- The `session.py` reference to `registry.ollama_schemas()` is unrelated to the removed `ollama` config field — do not touch it.
- `cli.py` needs no changes.
