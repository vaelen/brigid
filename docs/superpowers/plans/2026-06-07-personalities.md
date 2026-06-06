# Personalities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add named "personalities" — files holding a system prompt — that can be loaded at runtime with `/personality <name>` or selected at startup via a `[brigid].personality` config option.

**Architecture:** Path resolution and file loading live in `config.py` as pure methods on `Config` (resolved relative to the active config file). The REPL gains a `/personality` slash command and an active-personality marker on `_ActiveModel`; an active personality is "sticky" — it owns the system prompt and is re-applied over any profile selected with `/model`. The system prompt is injected per-stream in `llm.py`, so changes take effect on the next turn with no extra plumbing.

**Tech Stack:** Python 3.11, dataclasses, `pathlib`, `pytest` (`asyncio_mode = "auto"`), `ruff`, `pyright`.

---

## File Structure

- `src/brigid/config.py` — add `BrigidConfig.personality` field, parse it in `_build_brigid`, and add three `Config` methods: `personalities_dir()`, `load_personality()`, `list_personalities()`.
- `src/brigid/repl.py` — add `personality` field to `_ActiveModel`; add `/personality` to `_handle_slash`; make `/model` re-apply an active personality; make `/system` clear the marker; add a startup-application helper `_apply_startup_personality`; update `_HELP_TEXT`.
- `tests/test_config.py` — tests for the three new `Config` methods and the config field.
- `tests/test_repl.py` — tests for `/personality`, stickiness across `/model`, `/system` clearing the marker, and startup application.
- `config.example.toml` — document the `personality` option and personalities directory.
- `README.md` — add `/personality` row and a short personalities note.

---

## Task 1: `BrigidConfig.personality` config field

**Files:**
- Modify: `src/brigid/config.py` (the `BrigidConfig` dataclass ~line 43-46, and `_build_brigid` ~line 139-143)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_brigid_personality_field_parsed():
    cfg = from_dict({"brigid": {"personality": "luna"}})
    assert cfg.brigid.personality == "luna"


def test_brigid_personality_defaults_none():
    cfg = from_dict({})
    assert cfg.brigid.personality is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_brigid_personality_field_parsed -v`
Expected: FAIL — `BrigidConfig` has no attribute `personality` (TypeError or AttributeError).

- [ ] **Step 3: Add the field**

In `src/brigid/config.py`, change the `BrigidConfig` dataclass:

```python
@dataclass(frozen=True)
class BrigidConfig:
    default: str | None = None
    host: str = "http://localhost:11434"
    personality: str | None = None
```

And update `_build_brigid`:

```python
def _build_brigid(d: dict[str, Any]) -> BrigidConfig:
    return BrigidConfig(
        default=d.get("default"),
        host=d.get("host", BrigidConfig.host),
        personality=d.get("personality"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py::test_brigid_personality_field_parsed tests/test_config.py::test_brigid_personality_defaults_none -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add src/brigid/config.py tests/test_config.py
git commit -m "feat(config): add [brigid].personality option"
```

---

## Task 2: Personalities directory resolution + load + list

**Files:**
- Modify: `src/brigid/config.py` (add three methods to the `Config` class, after `active()` ~line 136)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_personalities_dir_relative_to_source_path(tmp_path):
    cfg = load(tmp_path / "config.toml")  # missing file: defaults, source_path set
    assert cfg.personalities_dir() == tmp_path / "personalities"


def test_personalities_dir_defaults_to_home(tmp_path, monkeypatch):
    cfg = Config()  # no source_path
    assert cfg.source_path is None
    expected = Path.home() / ".config" / "brigid" / "personalities"
    assert cfg.personalities_dir() == expected


def test_load_personality_match_precedence(tmp_path):
    pdir = tmp_path / "personalities"
    pdir.mkdir()
    (pdir / "luna.md").write_text("md body")
    (pdir / "luna.txt").write_text("txt body")
    (pdir / "luna").write_text("exact body")
    cfg = load(tmp_path / "config.toml")
    assert cfg.load_personality("luna") == "exact body"
    (pdir / "luna").unlink()
    assert cfg.load_personality("luna") == "md body"
    (pdir / "luna.md").unlink()
    assert cfg.load_personality("luna") == "txt body"


def test_load_personality_missing_returns_none(tmp_path):
    cfg = load(tmp_path / "config.toml")
    assert cfg.load_personality("nope") is None


def test_list_personalities_strips_extensions_and_sorts(tmp_path):
    pdir = tmp_path / "personalities"
    pdir.mkdir()
    (pdir / "luna.md").write_text("x")
    (pdir / "luna").write_text("x")  # dedupes with luna.md
    (pdir / "atlas.txt").write_text("x")
    (pdir / "zara").write_text("x")
    cfg = load(tmp_path / "config.toml")
    assert cfg.list_personalities() == ["atlas", "luna", "zara"]


def test_list_personalities_missing_dir_returns_empty(tmp_path):
    cfg = load(tmp_path / "config.toml")
    assert cfg.list_personalities() == []
```

Ensure `Path` is imported in the test module (the existing file imports from `brigid.config`; add `from pathlib import Path` near the top if not already present).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k personalit -v`
Expected: FAIL — `Config` has no attribute `personalities_dir` / `load_personality` / `list_personalities`.

- [ ] **Step 3: Implement the three methods**

In `src/brigid/config.py`, add to the `Config` class (after the `active` method):

```python
    def personalities_dir(self) -> Path:
        if self.source_path is not None:
            return self.source_path.parent / "personalities"
        return DEFAULT_CONFIG_PATH.parent / "personalities"

    def load_personality(self, name: str) -> str | None:
        base = self.personalities_dir()
        for candidate in (base / name, base / f"{name}.md", base / f"{name}.txt"):
            if candidate.is_file():
                return candidate.read_text()
        return None

    def list_personalities(self) -> list[str]:
        base = self.personalities_dir()
        if not base.is_dir():
            return []
        names: set[str] = set()
        for entry in base.iterdir():
            if not entry.is_file():
                continue
            stem = entry.name
            for ext in (".md", ".txt"):
                if stem.endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            names.add(stem)
        return sorted(names)
```

Note: `DEFAULT_CONFIG_PATH` is already defined at module top (`Path.home() / ".config" / "brigid" / "config.toml"`), so `DEFAULT_CONFIG_PATH.parent` is `~/.config/brigid`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k personalit -v`
Expected: PASS (all six).

- [ ] **Step 5: Commit**

```bash
git add src/brigid/config.py tests/test_config.py
git commit -m "feat(config): personalities dir resolution, load, and list"
```

---

## Task 3: `_ActiveModel.personality` marker + `/personality` command + `/model`/`/system` interaction

**Files:**
- Modify: `src/brigid/repl.py` (`_ActiveModel` ~line 40-43; `/model` block ~line 150-174; `/system` block ~line 175-185; add `/personality` block; `_HELP_TEXT` ~line 237-249)
- Test: `tests/test_repl.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_repl.py`. Note the existing module-level `RAW`, `_FakeRenderer`, and `_active` helpers; reuse them. These tests need a personalities directory, so they build a config with a `source_path` via `load`. Add these imports at the top of the test file if missing: `from pathlib import Path` and `from brigid.config import load` (the file already imports `from brigid.config import from_dict`).

```python
def _cfg_with_personalities(tmp_path, **files):
    pdir = tmp_path / "personalities"
    pdir.mkdir()
    for name, body in files.items():
        (pdir / name).write_text(body)
    cfg = load(tmp_path / "config.toml")  # missing file -> defaults, source_path set
    # give it the same two profiles RAW defines, so /model works
    cfg.models = from_dict(RAW).models
    cfg.brigid = from_dict(RAW).brigid
    return cfg


async def test_personality_load_sets_prompt_and_marker(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    r = _FakeRenderer()
    cont = await _handle_slash("/personality luna", cfg, active, None, None, r)
    assert cont is True
    assert active.cfg.system_prompt == "You are Luna."
    assert active.personality == "luna"


async def test_personality_not_found_lists_available(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    r = _FakeRenderer()
    await _handle_slash("/personality ghost", cfg, active, None, None, r)
    assert active.personality is None
    out = "\n".join(r.console.lines)
    assert "luna" in out


async def test_personality_none_clears(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/personality none", cfg, active, None, None, _FakeRenderer())
    assert active.cfg.system_prompt is None
    assert active.personality is None


async def test_personality_no_arg_shows_current_and_lists(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.", atlas="You are Atlas.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    r = _FakeRenderer()
    await _handle_slash("/personality", cfg, active, None, None, r)
    out = "\n".join(r.console.lines)
    assert "luna" in out
    assert "atlas" in out


async def test_personality_sticky_across_model_switch(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)  # starts on hermes (system_prompt "You are Hermes.")
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/model stheno", cfg, active, None, None, _FakeRenderer())
    assert active.name == "stheno"
    assert active.personality == "luna"
    assert active.cfg.system_prompt == "You are Luna."  # personality wins over profile


async def test_system_set_clears_personality_marker(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/system You are generic.", cfg, active, None, None, _FakeRenderer())
    assert active.personality is None
    assert active.cfg.system_prompt == "You are generic."


async def test_system_clear_clears_personality_marker(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/system clear", cfg, active, None, None, _FakeRenderer())
    assert active.personality is None
    assert active.cfg.system_prompt is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repl.py -k personality -v`
Expected: FAIL — `_ActiveModel` has no `personality` field / `/personality` is an unknown command.

- [ ] **Step 3: Add the `personality` field to `_ActiveModel`**

In `src/brigid/repl.py`:

```python
@dataclass
class _ActiveModel:
    name: str
    cfg: OllamaConfig
    personality: str | None = None
```

- [ ] **Step 4: Make `/model` re-apply an active personality**

In the `/model` handler, the existing success branch ends with building `ctx_note`/etc. and printing. After `active.cfg.tools = resolved.tools` and before computing the notes, re-apply the personality if one is active. Replace the success branch body (currently lines ~160-173) with:

```python
                active.name = name
                active.cfg.model = resolved.model
                active.cfg.host = resolved.host
                active.cfg.options = resolved.options
                active.cfg.system_prompt = resolved.system_prompt
                active.cfg.tools = resolved.tools
                if active.personality is not None:
                    body = cfg.load_personality(active.personality)
                    if body is not None:
                        active.cfg.system_prompt = body
                ctx = resolved.options.get("num_ctx")
                ctx_note = f", num_ctx={ctx}" if ctx is not None else ""
                host_note = f", host={resolved.host}" if resolved.host != cfg.brigid.host else ""
                tools_note = "" if resolved.tools else ", tools off"
                console.print(
                    f"model set to [bold]{name}[/bold] "
                    f"([dim]{resolved.model}{ctx_note}{host_note}{tools_note}[/dim])"
                )
```

- [ ] **Step 5: Make `/system` clear the personality marker**

Replace the `/system` block (currently ~lines 175-185) with:

```python
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
```

- [ ] **Step 6: Add the `/personality` block**

Insert immediately after the `/system` block:

```python
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
            console.print(
                f"unknown personality: [bold]{name}[/bold] — available: {available}"
            )
            return True
        active.cfg.system_prompt = body
        active.personality = name
        console.print(f"[dim]personality set to [bold]{name}[/bold] ({len(body)} chars)[/dim]")
        return True
```

- [ ] **Step 7: Update `_HELP_TEXT`**

In `_HELP_TEXT`, add a line after the `/system` line:

```
  /personality [name|none]  load a personality, clear it (none), or list available
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_repl.py -k personality -v`
Expected: PASS (all seven).

- [ ] **Step 9: Run the full repl + config suites to check for regressions**

Run: `uv run pytest tests/test_repl.py tests/test_config.py -v`
Expected: PASS (including the pre-existing `test_model_switch_applies_all_fields`, which still passes because no personality is active in that test).

- [ ] **Step 10: Commit**

```bash
git add src/brigid/repl.py tests/test_repl.py
git commit -m "feat(repl): /personality command, sticky across model switches"
```

---

## Task 4: Apply `[brigid].personality` at startup

**Files:**
- Modify: `src/brigid/repl.py` (add `_apply_startup_personality` helper; call it in `run()` after `active` is built ~line 74)
- Test: `tests/test_repl.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_repl.py` (reuses `_cfg_with_personalities`, `_active`, `_FakeRenderer` from earlier tasks; import `_apply_startup_personality` from `brigid.repl`):

```python
async def test_startup_personality_applied(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    cfg.brigid = from_dict({"brigid": {"default": "hermes", "personality": "luna"},
                            "models": RAW["models"]}).brigid
    active = _active(cfg)
    r = _FakeRenderer()
    _apply_startup_personality(cfg, active, r.console)
    assert active.personality == "luna"
    assert active.cfg.system_prompt == "You are Luna."


async def test_startup_personality_missing_warns_and_continues(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    cfg.brigid = from_dict({"brigid": {"default": "hermes", "personality": "ghost"},
                            "models": RAW["models"]}).brigid
    active = _active(cfg)
    before = active.cfg.system_prompt
    r = _FakeRenderer()
    _apply_startup_personality(cfg, active, r.console)
    assert active.personality is None
    assert active.cfg.system_prompt == before  # unchanged
    out = "\n".join(r.console.lines)
    assert "ghost" in out


async def test_startup_personality_none_is_noop(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    before = active.cfg.system_prompt
    _apply_startup_personality(cfg, active, _FakeRenderer().console)
    assert active.personality is None
    assert active.cfg.system_prompt == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repl.py -k startup_personality -v`
Expected: FAIL — `cannot import name '_apply_startup_personality'`.

- [ ] **Step 3: Add the helper**

In `src/brigid/repl.py`, add near the other module-level helpers (e.g. just above `async def run`):

```python
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
```

- [ ] **Step 4: Call it from `run()`**

In `run()`, immediately after these lines (~line 73-74):

```python
        active_name, active_cfg = cfg.active()
        active = _ActiveModel(active_name, active_cfg)
```

add:

```python
        _apply_startup_personality(cfg, active, console)
```

(Place it before `_print_banner(...)` so any warning prints above the banner.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_repl.py -k startup_personality -v`
Expected: PASS (all three).

- [ ] **Step 6: Commit**

```bash
git add src/brigid/repl.py tests/test_repl.py
git commit -m "feat(repl): apply [brigid].personality on startup"
```

---

## Task 5: Documentation (example config + README)

**Files:**
- Modify: `config.example.toml`
- Modify: `README.md`

- [ ] **Step 1: Update `config.example.toml`**

In the `[brigid]` table, add a `personality` line after `host`:

```toml
[brigid]
# Which model profile is active at startup (must name a [models.<name>] below).
# If omitted, the first defined profile is used.
default = "qwen"
# System-wide default Ollama host. Optional; each model may override it.
host    = "http://localhost:11434"
# Optional personality loaded at startup. Names a file in the personalities
# directory (a `personalities/` folder next to this config file). The file's
# contents become the system prompt and persist across `/model` switches.
# personality = "luna"
```

- [ ] **Step 2: Add a personalities note to `config.example.toml`**

After the `--- Models ---` block (before `[runtime]`), add:

```toml
# --- Personalities ---------------------------------------------------------
# A personality is a file whose contents become the system prompt. Put files in
# a `personalities/` directory next to this config (e.g. personalities/luna).
# In the REPL: `/personality luna` loads it, `/personality` lists them,
# `/personality none` clears it. An active personality overrides a profile's
# system_prompt and survives `/model` switches. Files may be named `<name>`,
# `<name>.md`, or `<name>.txt` (resolved in that order).
```

- [ ] **Step 3: Update the README slash-command table**

In `README.md`, add a row after the `/system` row (line ~30):

```markdown
| `/personality [name\|none]` | Load a personality file as the system prompt, clear it (`none`), or list available personalities |
```

- [ ] **Step 4: Verify the README table still aligns as plain text**

Read the table and confirm columns line up. Run: `uv run python -c "print(open('README.md').read()[400:1200])"` (or just Read the file) and eyeball the table.

- [ ] **Step 5: Commit**

```bash
git add config.example.toml README.md
git commit -m "docs: document personalities (config + README)"
```

---

## Task 6: Final verification

- [ ] **Step 1: Format**

Run: `uv run ruff format`
Expected: files reformatted/clean.

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: no errors.

- [ ] **Step 3: Type-check**

Run: `uv run pyright`
Expected: 0 errors. (If `console` param typing complains, annotate `_apply_startup_personality`'s `console` as it is used elsewhere — the repl already passes `renderer.console`; match the existing untyped style used by `_print_models`/`_print_banner`, which take an unannotated `console`.)

- [ ] **Step 4: Full test suite**

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Step 5: Commit any formatting-only changes**

```bash
git add -A
git commit -m "chore: format and lint personalities feature" || echo "nothing to commit"
```
