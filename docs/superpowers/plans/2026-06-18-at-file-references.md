# `@`-file references + tab completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let REPL users reference files with `@path` (attached as gated context on submit) and tab-complete `@<partial>` against files under the fs root.

**Architecture:** Two pieces, both confined to `repl.py` wiring plus a new `references.py` module — neither touches the agent loop (`session.py`) or transport (`llm.py`). A `prompt_toolkit` `Completer` handles live completion; an async `expand_references()` rewrites the submitted line, attaching file blocks after running each referenced file through the existing `fs.read:<path>` permission key. Path-confinement logic is extracted from `tools/builtin.py` into a shared `paths.py`.

**Tech Stack:** Python 3.11, prompt_toolkit, pytest (`asyncio_mode = "auto"`), ruff, pyright.

## Global Constraints

- Every module starts with `from __future__ import annotations`.
- Every new `.py` file starts with the two-line SPDX header:
  `# Copyright 2026 Andrew C. Young <andrew@vaelen.org>` / `# SPDX-License-Identifier: MIT`.
- Errors derive from `BrigidError`; path confinement raises `ToolError`.
- Tests are hermetic (no live Ollama). Async tests need no decorator.
- Gate sharing: `@`-references use the permission key `fs.read:<resolved-path>` verbatim.
- Quality gates before every commit: `uv run ruff format`, `uv run ruff check`, `uv run pyright`, `uv run pytest`.

---

### Task 1: Extract `resolve_path` into a shared `paths.py`

Lift the path-confinement helper out of `tools/builtin.py` so `references.py` can reuse it without depending on the tools package or duplicating logic. Behavior is identical; existing tests must still pass.

**Files:**
- Create: `src/brigid/paths.py`
- Modify: `src/brigid/tools/builtin.py` (replace the `_resolve` definition with an import alias)
- Test: existing suite (`tests/`) — no new test file; this is a behavior-preserving refactor

**Interfaces:**
- Produces: `resolve_path(root: Path, raw: str) -> Path` in `brigid.paths` — resolves `raw` against `root`, confines to `root` (unless `root` is `/`), raises `ToolError` on traversal escape. `builtin.py` keeps the name `_resolve` as an alias so its call sites are untouched.

- [ ] **Step 1: Create `src/brigid/paths.py`**

```python
# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

from brigid.errors import ToolError


def resolve_path(root: Path, raw: str) -> Path:
    """Resolve a (possibly relative) path against root and confine to root.

    Raises ToolError on traversal outside root. Root "/" disables confinement.
    """
    p = Path(raw).expanduser()
    p = (root / p).resolve() if not p.is_absolute() else p.resolve()
    root_resolved = root.resolve()
    if str(root_resolved) != "/":
        try:
            p.relative_to(root_resolved)
        except ValueError as e:
            raise ToolError(f"path {p} escapes configured fs root {root_resolved}") from e
    return p
```

- [ ] **Step 2: Replace the `_resolve` definition in `src/brigid/tools/builtin.py`**

Delete the existing `def _resolve(root, raw): ...` block (currently around lines 23-35) and the now-unused comment. Add this import alongside the other `brigid` imports near the top of the file:

```python
from brigid.paths import resolve_path as _resolve
```

Leave every existing `_resolve(...)` call site unchanged.

- [ ] **Step 3: Run the full suite to verify no regression**

Run: `uv run pytest`
Expected: PASS (same count as before the change; filesystem tool tests still green).

- [ ] **Step 4: Lint and type-check**

Run: `uv run ruff format && uv run ruff check && uv run pyright`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/brigid/paths.py src/brigid/tools/builtin.py
git commit -m "refactor: extract resolve_path into brigid.paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `expand_references` — gated `@`-file expansion

Add the async expansion function to a new `references.py`. Pure and hermetic; tested with a real `PermissionGate` over an in-memory `PermissionsConfig`.

**Files:**
- Create: `src/brigid/references.py`
- Test: `tests/test_references.py`

**Interfaces:**
- Consumes: `resolve_path` from `brigid.paths`; `PermissionGate.check(key: str) -> bool` from `brigid.permissions`.
- Produces: `async expand_references(line: str, root: Path, gate: PermissionGate) -> str | None` — returns `line` unchanged when there are no usable references, the augmented message when references resolve and are allowed, or `None` when any resolvable referenced file is denied.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_references.py`:

```python
# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

from brigid.config import PermissionsConfig
from brigid.permissions import PermissionGate
from brigid.references import expand_references


def _allow_gate() -> PermissionGate:
    return PermissionGate(PermissionsConfig(allow=["fs.read:*"]))


def _deny_gate() -> PermissionGate:
    # No allow patterns and no prompter -> PROMPT resolves to deny.
    return PermissionGate(PermissionsConfig())


async def test_no_references_returns_line_unchanged(tmp_path: Path) -> None:
    out = await expand_references("hello world", tmp_path, _allow_gate())
    assert out == "hello world"


async def test_expands_existing_file(tmp_path: Path) -> None:
    (tmp_path / "foo.txt").write_text("FOO BODY", encoding="utf-8")
    out = await expand_references("look at @foo.txt please", tmp_path, _allow_gate())
    assert out is not None
    assert "look at @foo.txt please" in out
    assert "--- @foo.txt ---" in out
    assert "FOO BODY" in out


async def test_nonexistent_left_literal(tmp_path: Path) -> None:
    out = await expand_references("ping @nope.txt", tmp_path, _allow_gate())
    assert out == "ping @nope.txt"


async def test_email_left_literal(tmp_path: Path) -> None:
    out = await expand_references("mail andrew@vaelen.org", tmp_path, _allow_gate())
    assert out == "mail andrew@vaelen.org"


async def test_midword_at_not_a_reference(tmp_path: Path) -> None:
    # Even when a file matching the tail exists, a mid-word @ is not a reference.
    (tmp_path / "vaelen.org").write_text("X", encoding="utf-8")
    out = await expand_references("mail andrew@vaelen.org", tmp_path, _allow_gate())
    assert out == "mail andrew@vaelen.org"


async def test_dedup_repeated_mention(tmp_path: Path) -> None:
    (tmp_path / "foo.txt").write_text("BODY", encoding="utf-8")
    out = await expand_references("@foo.txt and again @foo.txt", tmp_path, _allow_gate())
    assert out is not None
    assert out.count("--- @foo.txt ---") == 1


async def test_denied_file_returns_none(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("S", encoding="utf-8")
    out = await expand_references("show @secret.txt", tmp_path, _deny_gate())
    assert out is None


async def test_traversal_left_literal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    out = await expand_references("see @../passwd", root, _allow_gate())
    assert out == "see @../passwd"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_references.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'brigid.references'`.

- [ ] **Step 3: Write the implementation**

Create `src/brigid/references.py`:

```python
# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from pathlib import Path

from brigid.errors import ToolError
from brigid.paths import resolve_path
from brigid.permissions import PermissionGate

# A reference token: '@' at start-of-string or after whitespace, then a run
# of non-whitespace. Mid-word '@' (e.g. an email) is intentionally excluded.
_REF_RE = re.compile(r"(?:^|(?<=\s))@(\S+)")


async def expand_references(line: str, root: Path, gate: PermissionGate) -> str | None:
    """Expand @path references in `line` into appended file-context blocks.

    Returns `line` unchanged when no @token resolves to a readable file under
    `root`. Returns the augmented message when references resolve and are
    permitted. Returns None when any resolvable referenced file is denied by the
    gate (the caller should skip the turn).
    """
    mentions: dict[Path, str] = {}  # resolved path -> raw mention text (first win)
    order: list[Path] = []
    for match in _REF_RE.finditer(line):
        raw = match.group(1)
        try:
            resolved = resolve_path(root, raw)
        except ToolError:
            continue  # traversal escape -> leave literal
        if not resolved.is_file():
            continue  # typo / non-file (@vaelen.org, @property) -> leave literal
        if resolved in mentions:
            continue  # dedup repeated mentions
        if not await gate.check(f"fs.read:{resolved}"):
            return None  # denied -> abort turn
        mentions[resolved] = raw
        order.append(resolved)

    if not order:
        return line

    blocks: list[str] = []
    for path in order:
        contents = path.read_text(encoding="utf-8", errors="replace")
        blocks.append(f"--- @{mentions[path]} ---\n```\n{contents}\n```")
    return line + "\n\n" + "\n\n".join(blocks)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_references.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff format && uv run ruff check && uv run pyright`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/brigid/references.py tests/test_references.py
git commit -m "feat: gated @-file reference expansion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `AtFileCompleter` — tab completion for `@<partial>`

Add a `prompt_toolkit` completer to the same `references.py` (it co-locates with the other `@`-handling). Tested against a temp dir by driving `get_completions` directly.

**Files:**
- Modify: `src/brigid/references.py` (add the completer + its regex)
- Test: `tests/test_references.py` (append completer tests)

**Interfaces:**
- Consumes: `prompt_toolkit.completion.{Completer, Completion}`, `prompt_toolkit.document.Document`.
- Produces: `class AtFileCompleter(Completer)` constructed as `AtFileCompleter(root: Path)`; its `get_completions(document, complete_event)` yields `Completion` objects whose `.text` is the path relative to `root` (directories suffixed `/`), replacing only the fragment after `@`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_references.py`:

```python
from prompt_toolkit.document import Document  # noqa: E402  (grouped with new tests)

from brigid.references import AtFileCompleter  # noqa: E402


def _complete(completer: AtFileCompleter, text: str) -> list[str]:
    doc = Document(text)  # cursor defaults to end of text
    return [c.text for c in completer.get_completions(doc, None)]


def test_completer_matches_files(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("", encoding="utf-8")
    out = _complete(AtFileCompleter(tmp_path), "see @al")
    assert "alpha.txt" in out
    assert "beta.txt" not in out


def test_completer_dir_trailing_slash(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    out = _complete(AtFileCompleter(tmp_path), "@su")
    assert "sub/" in out


def test_completer_no_trigger_midword(tmp_path: Path) -> None:
    (tmp_path / "vaelen.org").write_text("", encoding="utf-8")
    out = _complete(AtFileCompleter(tmp_path), "andrew@vael")
    assert out == []


def test_completer_empty_frag_lists_root(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("", encoding="utf-8")
    out = _complete(AtFileCompleter(tmp_path), "@")
    assert "alpha.txt" in out
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_references.py -k completer -v`
Expected: FAIL with `ImportError: cannot import name 'AtFileCompleter'`.

- [ ] **Step 3: Add the completer to `src/brigid/references.py`**

Add these imports near the top (after the existing imports):

```python
from collections.abc import Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
```

Add this module-level regex below `_REF_RE`:

```python
# Token under the cursor: '@' at start or after whitespace, then non-whitespace,
# anchored to the cursor (end of the text before the cursor).
_CURSOR_REF_RE = re.compile(r"(?:^|\s)@(\S*)$")
```

Add the class at the end of the file:

```python
class AtFileCompleter(Completer):
    """Completes @<partial> tokens against files under the fs root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_completions(
        self, document: Document, complete_event: object
    ) -> Iterable[Completion]:
        match = _CURSOR_REF_RE.search(document.text_before_cursor)
        if match is None:
            return
        frag = match.group(1)
        if frag.endswith("/") or frag == "":
            base = self.root / frag
            prefix = ""
        else:
            target = self.root / frag
            base = target.parent
            prefix = target.name
        try:
            entries = sorted(base.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.name.startswith(prefix):
                continue
            try:
                rel = entry.relative_to(self.root)
            except ValueError:
                continue
            text = str(rel) + ("/" if entry.is_dir() else "")
            yield Completion(text, start_position=-len(frag))
```

- [ ] **Step 4: Run the completer tests to verify they pass**

Run: `uv run pytest tests/test_references.py -k completer -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the whole references suite, lint, type-check**

Run: `uv run pytest tests/test_references.py && uv run ruff format && uv run ruff check && uv run pyright`
Expected: all green (12 passed).

- [ ] **Step 6: Commit**

```bash
git add src/brigid/references.py tests/test_references.py
git commit -m "feat: @-file tab completion (AtFileCompleter)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire both pieces into the REPL

Attach the completer to the main input prompt (per-call, so permission/pattern prompts stay plain) and run each submitted user turn through `expand_references`.

**Files:**
- Modify: `src/brigid/repl.py` (imports near line 14-17; `run()` loop around lines 48-102)

**Interfaces:**
- Consumes: `expand_references`, `AtFileCompleter` from `brigid.references`; `cfg.tools.fs.root`.

- [ ] **Step 1: Add imports to `src/brigid/repl.py`**

Alongside the existing imports, add:

```python
from brigid.references import AtFileCompleter, expand_references
```

- [ ] **Step 2: Build the completer and fs root once, before the loop**

In `run()`, just before the `while True:` loop (after `_print_banner(...)`), add:

```python
        fs_root = Path(cfg.tools.fs.root)
        completer = AtFileCompleter(fs_root)
```

(`Path` is already imported in `repl.py`.)

- [ ] **Step 3: Pass the completer to the main prompt call**

Replace the existing `psession.prompt_async(...)` call in the loop with:

```python
                line = await psession.prompt_async(
                    FormattedText([("class:prompt", "you> ")]),
                    multiline=True,
                    completer=completer,
                    complete_while_typing=True,
                )
```

- [ ] **Step 4: Expand references before sending the turn**

Replace the block that currently reads:

```python
            renderer.assistant_label = active.personality or "brigid"
            session.add_user(line)
```

with:

```python
            expanded = await expand_references(line, fs_root, gate)
            if expanded is None:
                console.print("[dim]@-reference denied — turn skipped[/dim]")
                continue
            renderer.assistant_label = active.personality or "brigid"
            session.add_user(expanded)
```

- [ ] **Step 5: Verify the suite, lint, and type-check**

Run: `uv run pytest && uv run ruff format && uv run ruff check && uv run pyright`
Expected: all green.

- [ ] **Step 6: Manual smoke (informational)**

Note for the reviewer (no live model needed to see completion/expansion wiring):
Run `uv run brigid`, type `@` and confirm a completion menu lists files under the fs root; submit a turn containing `@<some-file>` and confirm the permission prompt uses the `fs.read:<path>` key. (Skip if no Ollama is configured — the gate prompt still appears before any model call.)

- [ ] **Step 7: Commit**

```bash
git add src/brigid/repl.py
git commit -m "feat: wire @-file references and completion into the REPL

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Tasks 2 and 3 both touch `references.py`; run them in order, not in parallel.
- The deny test relies on the gate's deny-by-default: a `PermissionGate` with no prompter resolves PROMPT to `False`.
- `expand_references` keys off `fs.read:<resolved>` exactly — do not invent a new key prefix, or `@`-references and the `fs.read` tool would diverge in policy.
