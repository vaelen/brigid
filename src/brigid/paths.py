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
