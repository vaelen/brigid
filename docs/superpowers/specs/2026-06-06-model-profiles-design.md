# Model profiles + upgraded `/model` command

**Date:** 2026-06-06
**Status:** Approved design (pending spec review)

## Problem

Brigid currently has a single active model defined in the `[ollama]` config block,
and a `/model <name>` command that swaps only the model *id* — leaving `num_ctx`,
sampling options, and the system prompt unchanged. This is wrong when models have
different requirements: e.g. Stheno needs `num_ctx = 8192`, Mag-Mell `16384`, and
Hermes `32768`. Switching models today silently keeps the previous context size and
samplers.

## Goal

Define a set of named **model profiles** in config, each carrying its own model id,
options (context size, samplers), optional system prompt, and optional host. Make
`/model <name>` switch to a profile and apply *all* of its settings at once.

## Decisions (settled during brainstorming)

1. **Replace the single-model `[ollama]` block** with a `[models.<name>]` table map.
   Profiles are **self-contained** (no inheritance/overlay between them).
2. **`host` is optional** with a system-wide default, **overridable per model**.
3. System-wide settings live in a **`[brigid]`** section (`default`, `host`).
4. **`/model` accepts only defined profiles.** An unknown name prints an error
   listing the available profiles and changes nothing (no arbitrary-model-id escape hatch).

## Config schema

```toml
[brigid]
default = "hermes"                  # optional: startup model. Omitted -> first [models.*]; none defined -> built-in default
host    = "http://localhost:11434"  # optional: system-wide default host (built-in fallback if omitted)

[runtime]
max_steps_per_turn  = 25
persist_permissions = true

[models.hermes]
model         = "hermes3-8b"        # optional: defaults to the profile name
system_prompt = "You are Hermes..." # optional
options       = { num_ctx = 32768, temperature = 0.7 }
# host        = "http://otherbox:11434"   # optional per-model override

[models.stheno]
model   = "stheno-8b"
options = { num_ctx = 8192, temperature = 1.1 }

[models.mag-mell]
model   = "mag-mell-12b"
options = { num_ctx = 16384, temperature = 1.25, min_p = 0.2 }
```

(`mag-mell` is a valid TOML bare key — hyphens are allowed.)

## Components

### `config.py`

- **New** `BrigidConfig(frozen)`: `default: str | None = None`, `host: str = "http://localhost:11434"`.
- **New** `ModelProfile(frozen)`: `name: str`, `model: str`, `options: dict[str, Any]`,
  `system_prompt: str | None`, `host: str | None`.
  - Built via `_build_models`. `model` defaults to the profile's table name when omitted.
  - `host = None` means "inherit `[brigid].host`".
- **`Config`** dataclass:
  - **Remove** the `ollama: OllamaConfig` field.
  - **Add** `brigid: BrigidConfig` and `models: dict[str, ModelProfile]` (insertion-ordered,
    preserving TOML order).
  - Keep `runtime`, `tools`, `permissions`, `mcp_servers`, `source_path`.
- **`OllamaConfig` is kept** as the mutable *runtime active-model* struct the backend reads
  and slash commands mutate. It is no longer built directly from a `[ollama]` block — it is
  produced by **resolving a profile**.
- **Resolution helpers** on `Config`:
  - `resolve(name: str) -> OllamaConfig | None` — look up a profile by name; build an
    `OllamaConfig(model, host = profile.host or brigid.host, options = dict(profile.options),
    system_prompt = profile.system_prompt)`. Returns `None` if the name is not a defined profile.
  - `active_model() -> OllamaConfig` — resolve `brigid.default`; if `default` is omitted, use the
    first defined profile; if no profiles are defined at all, return an `OllamaConfig` with built-in
    defaults (preserves "missing config file yields all-defaults" behavior).
  - `profile_names() -> list[str]` — for listing and error messages.
- **Fail-fast validation:** if `brigid.default` names a profile that does not exist, `from_dict`
  raises `ConfigError`.

### `llm.py`

- `OllamaBackend` recreates its `AsyncClient` when `self.cfg.host` changes between streams.
  Track the host the current client was built with; in `stream()`, if `self.cfg.host` differs,
  rebuild `self.client`. Keeps the backend transport-only and lets per-model host overrides take
  effect on the next turn after a switch.

### `repl.py` — `/model` command

- Resolve the initial active model in `run()` via `cfg.active_model()` and pass it to
  `OllamaBackend`.
- **`/model`** (no arg): print a table of profiles — name · model id · `num_ctx` · active marker —
  followed by the active profile name. Column widths aligned.
- **`/model <name>`**:
  - If `name` is a defined profile: resolve it and copy `model`, `host`, `options`,
    `system_prompt` onto the live active `OllamaConfig`. Print confirmation including the new
    `num_ctx` (and host if it differs from the default). Takes effect next turn.
  - If `name` is **not** a defined profile: print `unknown model: <name> — available: a, b, c`
    and change nothing.
- Update the banner to show the active profile name, and `/help` text for `/model`.

## Data flow

1. `load()` -> `from_dict()` builds `Config` with `brigid`, `models`.
2. `run()` calls `cfg.active_model()` -> initial `OllamaConfig` -> `OllamaBackend`.
3. User types `/model stheno` -> handler calls `cfg.resolve("stheno")` -> mutates the active
   `OllamaConfig` in place (same object the backend holds by reference).
4. Next turn: `OllamaBackend.stream()` reads the updated `model`/`options`/`system_prompt`, and
   rebuilds its client if `host` changed.

## Error handling

- Unknown `brigid.default` at load -> `ConfigError` (fail fast with a clear message).
- Unknown name to `/model` -> non-fatal REPL message listing available profiles.
- No models + no config file -> built-in default `OllamaConfig` (Brigid still runs).

## Testing

- **`tests/test_config.py`** (extend):
  - parse `[brigid]` + multiple `[models.*]`; order preserved.
  - `model` defaults to profile name when omitted.
  - per-model `host` override vs `[brigid].host` default vs built-in default.
  - `active_model()`: explicit default, omitted default -> first profile, no profiles -> built-in.
  - unknown `brigid.default` raises `ConfigError`.
  - `resolve()` returns `None` for unknown name.
- **`tests/test_llm.py`** (extend): client is rebuilt when `cfg.host` changes between `stream()`
  calls; not rebuilt when host is unchanged.
- **`tests/test_repl.py`** (new): `/model` with no arg lists profiles; `/model <known>` mutates the
  active `OllamaConfig` (model + options + system_prompt + host); `/model <unknown>` leaves it
  unchanged and reports available profiles.

## Migration

The user's live `~/.config/brigid/config.toml` currently uses `[ollama]`. As part of
implementation, rewrite it to the new schema with the three profiles (`hermes`, `stheno`,
`mag-mell`) matching the Modelfiles in `~/.config/brigid/modelfiles/`. The old `[ollama]` block
is ignored by the new loader.

## Out of scope (YAGNI)

- Persisting a `/model` switch back to config as the new default (switch is runtime-only).
- Per-profile inheritance/overlay.
- Hot-adding profiles at runtime via a slash command.
