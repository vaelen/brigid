# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import textwrap
from pathlib import Path

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
    assert cfg.models == {}
    assert cfg.brigid.default is None
    name, active = cfg.active()
    assert name == "default"
    assert active.model == OllamaConfig.model
    assert cfg.runtime.max_steps_per_turn == 25
    assert cfg.permissions.allow == []
    assert cfg.mcp_servers == []
    assert cfg.source_path == tmp_path / "missing.toml"


def test_load_valid_file(tmp_path):
    body = textwrap.dedent("""
        [brigid]
        default = "hermes"
        host    = "http://example:11434"

        [runtime]
        max_steps_per_turn = 10

        [models.hermes]
        model         = "hermes3-8b"
        system_prompt = "You are Hermes."
        options       = { num_ctx = 32768, temperature = 0.7 }

        [models.stheno]
        model   = "stheno-8b"
        options = { num_ctx = 8192 }

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
    assert cfg.brigid.default == "hermes"
    assert cfg.brigid.host == "http://example:11434"
    assert list(cfg.models) == ["hermes", "stheno"]
    name, active = cfg.active()
    assert name == "hermes"
    assert active.model == "hermes3-8b"
    assert active.host == "http://example:11434"
    assert active.options == {"num_ctx": 32768, "temperature": 0.7}
    assert active.system_prompt == "You are Hermes."
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


def test_model_defaults_to_profile_name():
    cfg = from_dict({"models": {"qwen": {"options": {"num_ctx": 4096}}}})
    assert cfg.models["qwen"].model == "qwen"
    resolved = cfg.resolve("qwen")
    assert resolved is not None
    assert resolved.model == "qwen"
    assert resolved.options == {"num_ctx": 4096}


def test_per_model_host_override_else_brigid_host():
    cfg = from_dict(
        {
            "brigid": {"host": "http://base:11434"},
            "models": {
                "a": {"model": "a"},
                "b": {"model": "b", "host": "http://other:11434"},
            },
        }
    )
    resolved_a = cfg.resolve("a")
    resolved_b = cfg.resolve("b")
    assert resolved_a is not None
    assert resolved_b is not None
    assert resolved_a.host == "http://base:11434"
    assert resolved_b.host == "http://other:11434"


def test_active_prefers_default_then_first_then_builtin():
    cfg = from_dict(
        {"brigid": {"default": "b"}, "models": {"a": {"model": "a"}, "b": {"model": "b"}}}
    )
    assert cfg.active()[0] == "b"

    cfg2 = from_dict({"models": {"a": {"model": "a"}, "b": {"model": "b"}}})
    assert cfg2.active()[0] == "a"

    cfg3 = from_dict({})
    name, active = cfg3.active()
    assert name == "default"
    assert active.model == OllamaConfig.model


def test_unknown_default_raises():
    with pytest.raises(ConfigError):
        from_dict({"brigid": {"default": "ghost"}, "models": {"a": {"model": "a"}}})


def test_resolve_unknown_returns_none():
    cfg = from_dict({"models": {"a": {"model": "a"}}})
    assert cfg.resolve("nope") is None


def test_tools_flag_defaults_true_and_overrides():
    cfg = from_dict({"models": {"a": {"model": "a"}, "b": {"model": "b", "tools": False}}})
    assert cfg.models["a"].tools is True
    assert cfg.models["b"].tools is False
    resolved_a = cfg.resolve("a")
    resolved_b = cfg.resolve("b")
    assert resolved_a is not None
    assert resolved_b is not None
    assert resolved_a.tools is True
    assert resolved_b.tools is False


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


def test_env_substitution_missing_var_yields_empty(monkeypatch):
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


def test_brigid_personality_field_parsed():
    cfg = from_dict({"brigid": {"personality": "luna"}})
    assert cfg.brigid.personality == "luna"


def test_brigid_personality_defaults_none():
    cfg = from_dict({})
    assert cfg.brigid.personality is None


def test_personalities_dir_relative_to_source_path(tmp_path):
    cfg = load(tmp_path / "config.toml")  # missing file: defaults, source_path set
    assert cfg.personalities_dir() == tmp_path / "personalities"


def test_personalities_dir_defaults_to_home(tmp_path, monkeypatch):
    cfg = Config()  # no source_path
    assert cfg.source_path is None
    expected = Path.home() / ".config" / "brigid" / "personalities"
    assert cfg.personalities_dir() == expected


def test_load_personality_match_precedence(tmp_path):
    pdir = tmp_path / "personalities"
    pdir.mkdir()
    (pdir / "luna.md").write_text("md body")
    (pdir / "luna.txt").write_text("txt body")
    (pdir / "luna").write_text("exact body")
    cfg = load(tmp_path / "config.toml")
    assert cfg.load_personality("luna") == "exact body"
    (pdir / "luna").unlink()
    assert cfg.load_personality("luna") == "md body"
    (pdir / "luna.md").unlink()
    assert cfg.load_personality("luna") == "txt body"


def test_load_personality_missing_returns_none(tmp_path):
    cfg = load(tmp_path / "config.toml")
    assert cfg.load_personality("nope") is None


def test_list_personalities_strips_extensions_and_sorts(tmp_path):
    pdir = tmp_path / "personalities"
    pdir.mkdir()
    (pdir / "luna.md").write_text("x")
    (pdir / "luna").write_text("x")  # dedupes with luna.md
    (pdir / "atlas.txt").write_text("x")
    (pdir / "zara").write_text("x")
    cfg = load(tmp_path / "config.toml")
    assert cfg.list_personalities() == ["atlas", "luna", "zara"]


def test_list_personalities_missing_dir_returns_empty(tmp_path):
    cfg = load(tmp_path / "config.toml")
    assert cfg.list_personalities() == []
