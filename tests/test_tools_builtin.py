# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import httpx
import pytest

from brigid.config import BashToolsConfig, FsToolsConfig, WebToolsConfig
from brigid.errors import ToolError
from brigid.tools.builtin import (
    Bash,
    FsEdit,
    FsList,
    FsRead,
    FsWrite,
    WebFetch,
)

# --------------------------- fs root confinement ---------------------------


def _fs_cfg(root) -> FsToolsConfig:
    return FsToolsConfig(root=str(root))


@pytest.mark.asyncio
async def test_fs_read_resolves_relative_to_root(tmp_path):
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    tool = FsRead(_fs_cfg(tmp_path))
    assert await tool.run(path="hello.txt") == "hi"


@pytest.mark.asyncio
async def test_fs_read_blocks_traversal_outside_root(tmp_path):
    inside = tmp_path / "sub"
    inside.mkdir()
    (tmp_path / "outside.txt").write_text("nope", encoding="utf-8")
    tool = FsRead(_fs_cfg(inside))
    with pytest.raises(ToolError, match="escapes"):
        await tool.run(path="../outside.txt")


@pytest.mark.asyncio
async def test_fs_write_creates_parents(tmp_path):
    tool = FsWrite(_fs_cfg(tmp_path))
    out = await tool.run(path="a/b/c.txt", content="data")
    assert "wrote 4 bytes" in out
    assert (tmp_path / "a/b/c.txt").read_text() == "data"


@pytest.mark.asyncio
async def test_fs_edit_first_and_all(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("foo bar foo", encoding="utf-8")
    tool = FsEdit(_fs_cfg(tmp_path))
    await tool.run(path="x.txt", old="foo", new="baz")
    assert f.read_text() == "baz bar foo"
    await tool.run(path="x.txt", old="foo", new="qux", replace_all=True)
    assert f.read_text() == "baz bar qux"


@pytest.mark.asyncio
async def test_fs_edit_missing_substring_raises(tmp_path):
    f = tmp_path / "y.txt"
    f.write_text("hello", encoding="utf-8")
    tool = FsEdit(_fs_cfg(tmp_path))
    with pytest.raises(ToolError, match="not found"):
        await tool.run(path="y.txt", old="absent", new="x")


@pytest.mark.asyncio
async def test_fs_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    tool = FsList(_fs_cfg(tmp_path))
    out = await tool.run(path=".")
    assert "a.txt" in out
    assert "sub/" in out


def test_fs_permission_keys_use_resolved_paths(tmp_path):
    (tmp_path / "z.txt").write_text("", encoding="utf-8")
    rd = FsRead(_fs_cfg(tmp_path))
    key = rd.permission_key({"path": "z.txt"})
    assert key.startswith("fs.read:")
    assert str((tmp_path / "z.txt").resolve()) in key


# --------------------------- bash ---------------------------


@pytest.mark.asyncio
async def test_bash_runs_command():
    tool = Bash(BashToolsConfig(timeout_seconds=5))
    out = await tool.run(command="echo hello")
    assert "exit_code: 0" in out
    assert "hello" in out


@pytest.mark.asyncio
async def test_bash_captures_stderr_and_nonzero_exit():
    tool = Bash(BashToolsConfig(timeout_seconds=5))
    out = await tool.run(command="ls /definitely-does-not-exist-xyz")
    assert "exit_code: " in out
    assert "stderr:" in out


@pytest.mark.asyncio
async def test_bash_timeout_kills_process():
    tool = Bash(BashToolsConfig(timeout_seconds=1))
    with pytest.raises(ToolError, match="timed out"):
        await tool.run(command="sleep 10")


def test_bash_permission_key():
    tool = Bash(BashToolsConfig())
    assert tool.permission_key({"command": "git status"}) == "bash:git status"


# --------------------------- web fetch ---------------------------


class _FakeTransport(httpx.MockTransport):
    pass


@pytest.mark.asyncio
async def test_web_fetch_truncates(monkeypatch):
    big_body = b"x" * 5_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=big_body,
            headers={"content-type": "text/plain"},
        )

    transport = _FakeTransport(handler)

    # Patch httpx.AsyncClient to use our transport
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("follow_redirects", None)
        kwargs.pop("timeout", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    tool = WebFetch(WebToolsConfig(max_bytes=1_000, timeout_seconds=5))
    out = await tool.run(url="https://example.com/")
    assert "status: 200" in out
    # body sits between the blank line and the truncation marker
    body_part = out.split("\n\n", 1)[1].split("\n[truncated", 1)[0]
    assert body_part == "x" * 1_000
    assert "[truncated" in out


@pytest.mark.asyncio
async def test_web_fetch_full_when_under_cap(monkeypatch):
    body = b"hello"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/plain"})

    transport = _FakeTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("follow_redirects", None)
        kwargs.pop("timeout", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    tool = WebFetch(WebToolsConfig(max_bytes=1_000, timeout_seconds=5))
    out = await tool.run(url="https://example.com/")
    assert "[truncated" not in out
    assert "hello" in out


def test_web_permission_key():
    tool = WebFetch(WebToolsConfig())
    assert tool.permission_key({"url": "https://x"}) == "web.fetch:https://x"


# --------------------------- registry ---------------------------


def test_registry_collisions():
    from brigid.errors import BrigidError
    from brigid.tools import ToolRegistry

    reg = ToolRegistry.empty()
    reg.register(Bash(BashToolsConfig()))
    with pytest.raises(BrigidError, match="duplicate"):
        reg.register(Bash(BashToolsConfig()))


def test_registry_ollama_schemas():
    from brigid.tools import ToolRegistry

    reg = ToolRegistry.empty()
    reg.register(Bash(BashToolsConfig()))
    schemas = reg.ollama_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "bash"
    assert "command" in schemas[0]["function"]["parameters"]["properties"]
