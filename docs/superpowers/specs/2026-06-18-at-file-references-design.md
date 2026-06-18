# `@`-file references + tab completion — design

**Date:** 2026-06-18
**Status:** Approved

## Goal

Let users reference files from the REPL prompt with `@path`. On submit, each
referenced file's contents are attached as context to the outgoing user turn,
gated by the same permission check that `fs.read` uses. While typing, `@<partial>`
offers tab completion over files under the configured fs root.

This is an ergonomic shortcut for pulling files in *eagerly*, without a model
round-trip through the `fs.read` tool. The model can still read files itself; `@`
just front-loads files the user already knows they want.

## Two independent pieces

Both are wired in `repl.py`. Neither touches the agent loop (`session.py`) or the
model transport (`llm.py`).

### 1. Tab completion — `AtFileCompleter`

A `prompt_toolkit` `Completer` passed to the existing `PromptSession`
(`repl.py:53`), rooted at `cfg.tools.fs.root`.

- Triggers only on the token under the cursor matching `@\S*`. The `@` must start
  the token (start-of-line or preceded by whitespace), so `andrew@vaelen.org`
  mid-word never triggers.
- Globs `root/<partial>*` and yields matches. Directories get a trailing `/` (so
  you can keep tabbing deeper); files do not.
- `complete_while_typing=True` — matches appear live; Tab also completes.
- Confined to the fs root, matching the tools' own confinement.

Pure input ergonomics: it inserts text and nothing more. It does **not** itself
expand or read files.

### 2. `@`-expansion — `expand_references`

A new module `src/brigid/references.py` exposing one async function:

```python
async def expand_references(line: str, root: Path, gate: PermissionGate) -> str | None
```

Called in the REPL loop *after* reading the line and *after* slash-command /
empty-line handling, but *before* `session.add_user(...)`.

Behavior:

1. Tokenize `@`-tokens: a `@` that is start-anchored or whitespace-preceded,
   followed by a run of non-whitespace characters.
2. For each token, `_resolve(root, token[1:])`. If it does **not** resolve to an
   existing regular file, leave the token as literal text — no warning. This
   covers typos, emails (`@vaelen.org`), and decorators (`@property`).
3. For each token that *does* resolve to a regular file, call
   `gate.check(f"fs.read:{resolved}")` — the **same permission key** `fs.read`
   uses, so allow/deny policies are shared between the two.
   - **If any referenced, resolvable file is denied → return `None`.** The caller
     prints a dim notice and skips the turn. Nothing reaches the model.
4. If all resolvable references are allowed, build the outgoing message:
   - The user's typed line, unchanged, first.
   - Then one appended block per **unique** resolved file:

     ```
     <user's typed line>

     --- @src/foo.py ---
     ```
     <file contents>
     ```
     ```

   - Dedup repeated mentions of the same resolved path.
   - The `@mention` stays visible in the typed line.

`_resolve` is promoted from module-private in `tools/builtin.py` to a shared
import (re-export or lift to a small shared location) rather than duplicated.
`ToolError` raised by `_resolve` on traversal is caught and treated as
"not a usable reference" → left literal (consistent with rule 2).

### Loop change — `repl.py`

```python
line = await psession.prompt_async(...)
stripped = line.strip()
if not stripped:
    continue
if stripped.startswith("/"):
    ...  # slash handling, unchanged
    continue

expanded = await expand_references(line, Path(cfg.tools.fs.root), gate)
if expanded is None:
    console.print("[dim]@-reference denied — turn skipped[/dim]")
    continue
session.add_user(expanded)
```

`@`-expansion applies only to real user turns. Slash commands and empty lines are
handled before it and are unaffected.

## Testing

All hermetic — no live Ollama.

- **`references.py`** is the main target:
  - tokenizing: start/whitespace-anchored `@`, mid-word `@` ignored;
  - non-resolving tokens (typos, `@vaelen.org`, `@property`) left literal;
  - traversal-escaping tokens left literal;
  - dedup of repeated mentions;
  - the appended-block format (header + fenced contents);
  - gate-deny on any resolvable file → returns `None` (fake gate);
  - all-allowed → expanded string (fake gate).
- **`AtFileCompleter`**: a few unit tests over `get_completions` against a temp
  dir — real-file matches, trailing `/` on directories, no trigger mid-word.

## Out of scope (YAGNI)

- Directory-content expansion (`@somedir/` attaching all files).
- Glob expansion in references (`@src/*.py`).
- Paths with spaces / quoting.
- Caching of file reads.

Files only, one path per `@`-token.
