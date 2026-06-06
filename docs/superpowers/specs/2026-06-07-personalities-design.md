# Personalities — Design

## Summary

Add **personalities** to Brigid: named files, each holding a system prompt, that can
be swapped in quickly at runtime or selected at startup. A personality is effectively
a fast way to switch the system prompt by name.

`/personality luna` loads the file `luna` from the personalities directory into the
active model's system prompt. Once active, a personality is **sticky**: it owns the
system prompt and survives `/model` switches, re-applying itself over whatever profile
is selected.

## Personalities directory

Resolved relative to the active config file:

- If `Config.source_path` is set → `source_path.parent / "personalities"`
- Else (all-defaults, no config file loaded) → `~/.config/brigid/personalities`

Exposed as a new method `Config.personalities_dir() -> Path`.

## File matching

For a name like `luna`, the following candidates are tried in order; the first that
exists wins:

1. `personalities/luna`        (exact)
2. `personalities/luna.md`
3. `personalities/luna.txt`

## New helpers in `config.py`

Pure and independently testable:

- `Config.personalities_dir() -> Path` — resolution described above.
- `Config.load_personality(name: str) -> str | None` — returns the file contents of
  the first matching candidate, or `None` if none exists.
- `Config.list_personalities() -> list[str]` — scans the directory, strips `.md`/`.txt`
  extensions, dedupes, and returns a sorted list of names. Returns `[]` if the
  directory is missing.

## Config option

Add `personality: str | None = None` to `BrigidConfig` (the `[brigid]` table,
alongside `default`). Parsed in `Config.from_dict`.

At startup (in `repl.run`), after the active model is built: if
`cfg.brigid.personality` is set, load it via `load_personality` and apply its contents
to `active.cfg.system_prompt`, setting the active-personality marker. A missing file
at startup prints a warning and continues (non-fatal, consistent with MCP failure
handling).

## Active-personality tracking

Add `personality: str | None = None` to `_ActiveModel` in `repl.py`. This records which
personality (if any) currently owns the system prompt, so the display can be honest and
so `/model` knows to re-apply it.

Lifecycle:

- **Set** by `/personality <name>` (runtime) and by the startup config option.
- **Cleared** by `/system` (set or clear) and by `/personality none` — i.e. any manual
  override of the prompt drops the marker.
- **Preserved** across `/model` switches.

## Slash command: `/personality`

Added to `_handle_slash` in `repl.py`.

- **No arg** → print the active personality (or `(none)`) plus a short note on the
  current system prompt, then list available personality names (mirrors `/model`'s
  "show current + list").
- **`none`** (reserved keyword) → clear the system prompt (`system_prompt = None`) and
  the personality marker. Same effect as `/system clear`. Because `none` is reserved, a
  personality file literally named `none` cannot be selected.
- **`<name>`** → load via `load_personality`. On success: set
  `active.cfg.system_prompt` to the contents, set the marker to `<name>`, and print a
  confirmation (name + char count). On not found: print an error line followed by the
  available list.

## Interaction with `/model`

The existing `/model` handler sets `active.cfg.system_prompt = resolved.system_prompt`.
After applying the profile, if `active.personality` is set, re-load that personality and
overwrite `system_prompt` with its contents (the personality wins). The marker is left
intact.

## Interaction with `/system`

The existing `/system` handler (show / set `<text>` / `clear`) additionally clears the
active-personality marker on both set and clear, since the user has manually overridden
the prompt.

## Effect timing

No special plumbing for "take effect": the system prompt is injected per-stream in
`llm.py`, so any change to `active.cfg.system_prompt` applies on the next turn — same as
`/system` today.

## Help text

Add a line to `_HELP_TEXT`:

```
/personality [name|none]  load a personality, clear it, or list available
```

## Example config

Update the example config / README to document the `[brigid]` `personality` option and
the personalities directory convention.

## Testing

Hermetic unit tests (using `tmp_path`):

- **Directory resolution** — with `source_path` set (→ sibling `personalities/`) and
  without (→ `~/.config/brigid/personalities`).
- **Match precedence** — exact beats `.md` beats `.txt`; `None` when nothing matches.
- **Listing** — extension stripping, dedupe across `luna` + `luna.md`, sorted order,
  empty list when the directory is missing.
- **`_handle_slash` for `/personality`** — load success (sets system_prompt + marker),
  `none` clears both, not-found path, and bare command output.
- **Stickiness** — a `/model` switch with an active personality keeps the personality's
  system prompt rather than the profile's.
- **`/system` clears the marker.**
- **Startup application** — `cfg.brigid.personality` set applies the personality; a
  missing file warns and continues.
