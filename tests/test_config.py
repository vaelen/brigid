# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import textwrap

import pytest

from brigid.config import (
    Config,
    MCPServerConfig,
    OllamaConfig,
    from_dict,
    load,
)
from brigid.errors import ConfigError


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert isinstance(cfg, Config)
    assert cfg.ollama.model == OllamaConfig.model
    assert cfg.runtime.max_steps_per_turn == 25
    assert cfg.permissions.allow == []
    assert cfg.mcp_servers == []
    assert cfg.source_path == tmp_path / "missing.toml"


def test_load_valid_file(tmp_path):
    body = textwrap.dedent("""
        [ollama]
        model = "qwen3.6:35b-a3b"
        host  = "http://example:11434"
        options = { temperature = 0.4 }

        [runtime]
        max_steps_per_turn = 10

        [permissions]
        allow = ["fs.read:*"]
        deny  = ["bash:rm -rf *"]

        [[mcp.servers]]
        name = "fs"
        command = "npx"
        args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    """)
    path = tmp_path / "c.toml"
    path.write_text(body)
    cfg = load(path)
    assert cfg.ollama.model == "qwen3.6:35b-a3b"
    assert cfg.ollama.host == "http://example:11434"
    assert cfg.ollama.options == {"temperature": 0.4}
    assert cfg.runtime.max_steps_per_turn == 10
    assert cfg.permissions.allow == ["fs.read:*"]
    assert cfg.permissions.deny == ["bash:rm -rf *"]
    assert cfg.mcp_servers == [
        MCPServerConfig(
            name="fs",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={},
        )
    ]


def test_env_substitution(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    body = textwrap.dedent("""
        [[mcp.servers]]
        name = "gh"
        command = "uvx"
        args = ["mcp-server-github"]
        env = { GITHUB_TOKEN = "${env:MY_TOKEN}" }
    """)
    path = tmp_path / "c.toml"
    path.write_text(body)
    cfg = load(path)
    assert cfg.mcp_servers[0].env == {"GITHUB_TOKEN": "s3cret"}


def test_env_substitution_missing_var_yields_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("UNSET_FOR_TEST", raising=False)
    raw = {
        "mcp": {
            "servers": [{"name": "x", "command": "echo", "env": {"V": "${env:UNSET_FOR_TEST}"}}]
        }
    }
    cfg = from_dict(raw)
    assert cfg.mcp_servers[0].env == {"V": ""}


def test_invalid_mcp_server_raises():
    with pytest.raises(ConfigError):
        from_dict({"mcp": {"servers": [{"name": "no-cmd"}]}})


def test_malformed_toml_raises(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("not = valid = toml\n")
    with pytest.raises(ConfigError):
        load(path)
