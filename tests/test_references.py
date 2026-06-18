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
