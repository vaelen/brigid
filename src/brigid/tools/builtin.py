# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from brigid.config import BashToolsConfig, FsToolsConfig, WebToolsConfig
from brigid.errors import ToolError
from brigid.tools import Tool

# ----------------------------------------------------------------------------
# Filesystem helpers
# ----------------------------------------------------------------------------

_RESULT_PREVIEW_BYTES = 4_000  # for inline display when results get echoed


def _resolve(root: Path, raw: str) -> Path:
    """Resolve a (possibly relative) path against root and confine to root.
    Raises ToolError on traversal outside root."""
    p = Path(raw).expanduser()
    p = (root / p).resolve() if not p.is_absolute() else p.resolve()
    root_resolved = root.resolve()
    # Allow if root is "/" (no confinement) or path is inside root.
    if str(root_resolved) != "/":
        try:
            p.relative_to(root_resolved)
        except ValueError as e:
            raise ToolError(f"path {p} escapes configured fs root {root_resolved}") from e
    return p


# ----------------------------------------------------------------------------
# Filesystem tools
# ----------------------------------------------------------------------------


class FsRead(Tool):
    name = "fs.read"
    description = (
        "Read the full contents of a UTF-8 text file. Returns the file body as a string. "
        "Use this to inspect a file you want to read or edit."
    )

    def __init__(self, cfg: FsToolsConfig) -> None:
        self.root = Path(cfg.root)
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (absolute, or relative to the configured fs root).",
                }
            },
            "required": ["path"],
        }

    def permission_key(self, args: dict[str, Any]) -> str:
        return f"fs.read:{_resolve(self.root, args['path'])}"

    async def run(self, **args: Any) -> str:
        path = _resolve(self.root, args["path"])
        if not path.exists():
            raise ToolError(f"file not found: {path}")
        if not path.is_file():
            raise ToolError(f"not a regular file: {path}")
        return path.read_text(encoding="utf-8", errors="replace")


class FsWrite(Tool):
    name = "fs.write"
    description = (
        "Overwrite a file with the given content (creates parent directories if needed). "
        "Returns the byte count written."
    )

    def __init__(self, cfg: FsToolsConfig) -> None:
        self.root = Path(cfg.root)
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination path."},
                "content": {"type": "string", "description": "Full file contents to write."},
            },
            "required": ["path", "content"],
        }

    def permission_key(self, args: dict[str, Any]) -> str:
        return f"fs.write:{_resolve(self.root, args['path'])}"

    async def run(self, **args: Any) -> str:
        path = _resolve(self.root, args["path"])
        content: str = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {len(content.encode('utf-8'))} bytes to {path}"


class FsEdit(Tool):
    name = "fs.edit"
    description = (
        "Replace the first (or all) occurrences of `old` with `new` in a text file. "
        "Fails if `old` is not present."
    )

    def __init__(self, cfg: FsToolsConfig) -> None:
        self.root = Path(cfg.root)
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to edit."},
                "old": {"type": "string", "description": "Exact substring to find."},
                "new": {"type": "string", "description": "Replacement substring."},
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replace every occurrence; otherwise only the first.",
                    "default": False,
                },
            },
            "required": ["path", "old", "new"],
        }

    def permission_key(self, args: dict[str, Any]) -> str:
        return f"fs.edit:{_resolve(self.root, args['path'])}"

    async def run(self, **args: Any) -> str:
        path = _resolve(self.root, args["path"])
        old: str = args["old"]
        new: str = args["new"]
        replace_all: bool = bool(args.get("replace_all", False))
        if not path.is_file():
            raise ToolError(f"not a file: {path}")
        body = path.read_text(encoding="utf-8")
        if old not in body:
            raise ToolError(f"`old` substring not found in {path}")
        body = body.replace(old, new) if replace_all else body.replace(old, new, 1)
        path.write_text(body, encoding="utf-8")
        n = body.count(new) if replace_all else 1  # informational
        return f"edited {path} ({n} replacement{'s' if n != 1 else ''})"


class FsList(Tool):
    name = "fs.list"
    description = (
        "List entries in a directory (one name per line, with a trailing '/' for subdirs). "
        "Useful for browsing the file tree."
    )

    def __init__(self, cfg: FsToolsConfig) -> None:
        self.root = Path(cfg.root)
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path. Defaults to the configured fs root.",
                    "default": ".",
                }
            },
        }

    def permission_key(self, args: dict[str, Any]) -> str:
        return f"fs.list:{_resolve(self.root, args.get('path', '.'))}"

    async def run(self, **args: Any) -> str:
        path = _resolve(self.root, args.get("path", "."))
        if not path.exists():
            raise ToolError(f"path not found: {path}")
        if not path.is_dir():
            raise ToolError(f"not a directory: {path}")
        entries = sorted(path.iterdir(), key=lambda p: p.name)
        lines = [f"{p.name}/" if p.is_dir() else p.name for p in entries]
        return "\n".join(lines) if lines else "(empty)"


# ----------------------------------------------------------------------------
# Bash
# ----------------------------------------------------------------------------


class Bash(Tool):
    name = "bash"
    description = (
        "Run a shell command via /bin/sh -c. Captures stdout+stderr and reports exit code. "
        "Use this for small, well-defined operations (git, ls, grep, etc.)."
    )

    def __init__(self, cfg: BashToolsConfig) -> None:
        self.timeout = cfg.timeout_seconds
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
            },
            "required": ["command"],
        }

    def permission_key(self, args: dict[str, Any]) -> str:
        return f"bash:{args['command']}"

    async def run(self, **args: Any) -> str:
        cmd: str = args["command"]
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ToolError(f"bash command timed out after {self.timeout}s: {cmd}") from None
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        rc = proc.returncode
        parts = [f"exit_code: {rc}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)


# ----------------------------------------------------------------------------
# Web fetch
# ----------------------------------------------------------------------------


class WebFetch(Tool):
    name = "web.fetch"
    description = (
        "HTTP GET a URL and return the response body as text (truncated to a configured byte cap). "
        "Follows redirects."
    )

    def __init__(self, cfg: WebToolsConfig) -> None:
        self.max_bytes = cfg.max_bytes
        self.timeout = cfg.timeout_seconds
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
            },
            "required": ["url"],
        }

    def permission_key(self, args: dict[str, Any]) -> str:
        return f"web.fetch:{args['url']}"

    async def run(self, **args: Any) -> str:
        url: str = args["url"]
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout) as client:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as e:
                raise ToolError(f"web fetch failed for {url}: {e}") from e
        body = resp.content[: self.max_bytes]
        truncated = len(resp.content) > self.max_bytes
        try:
            text = body.decode(resp.encoding or "utf-8", errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = body.decode("utf-8", errors="replace")
        suffix = (
            f"\n[truncated; received {len(resp.content)} bytes, kept {self.max_bytes}]"
            if truncated
            else ""
        )
        ctype = resp.headers.get("content-type", "")
        return f"status: {resp.status_code}\ncontent-type: {ctype}\n\n{text}{suffix}"


# ----------------------------------------------------------------------------
# Convenience: build all built-ins from a Config
# ----------------------------------------------------------------------------


def builtin_tools(
    fs_cfg: FsToolsConfig,
    bash_cfg: BashToolsConfig,
    web_cfg: WebToolsConfig,
) -> list[Tool]:
    return [
        FsRead(fs_cfg),
        FsWrite(fs_cfg),
        FsEdit(fs_cfg),
        FsList(fs_cfg),
        Bash(bash_cfg),
        WebFetch(web_cfg),
    ]


__all__ = [
    "Bash",
    "FsEdit",
    "FsList",
    "FsRead",
    "FsWrite",
    "WebFetch",
    "builtin_tools",
]
