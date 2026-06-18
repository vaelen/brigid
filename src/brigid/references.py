# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from brigid.errors import ToolError
from brigid.paths import resolve_path
from brigid.permissions import PermissionGate

# A reference token: '@' at start-of-string or after whitespace, then a run
# of non-whitespace. Mid-word '@' (e.g. an email) is intentionally excluded.
_REF_RE = re.compile(r"(?:^|(?<=\s))@(\S+)")

# Token under the cursor: '@' at start or after whitespace, then non-whitespace,
# anchored to the cursor (end of the text before the cursor).
_CURSOR_REF_RE = re.compile(r"(?:^|\s)@(\S*)$")


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


class AtFileCompleter(Completer):
    """Completes @<partial> tokens against files under the fs root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_completions(self, document: Document, complete_event: object) -> Iterable[Completion]:
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
