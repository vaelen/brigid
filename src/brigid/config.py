# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brigid.errors import ConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "brigid" / "config.toml"

_ENV_PATTERN = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: str) -> str:
    return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _expand_env_in(obj: Any) -> Any:
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, list):
        return [_expand_env_in(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _expand_env_in(v) for k, v in obj.items()}
    return obj


@dataclass
class OllamaConfig:
    model: str = "qwen3.6:35b-a3b"
    host: str = "http://localhost:11434"
    options: dict[str, Any] = field(default_factory=dict)
    system_prompt: str | None = None
    tools: bool = True  # when False, the backend never offers tools to this model


@dataclass(frozen=True)
class BrigidConfig:
    default: str | None = None
    host: str = "http://localhost:11434"
    personality: str | None = None


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    system_prompt: str | None = None
    host: str | None = None
    tools: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    max_steps_per_turn: int = 25
    persist_permissions: bool = True


@dataclass(frozen=True)
class FsToolsConfig:
    root: str = "."


@dataclass(frozen=True)
class BashToolsConfig:
    timeout_seconds: int = 60


@dataclass(frozen=True)
class WebToolsConfig:
    max_bytes: int = 200_000
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ToolsConfig:
    fs: FsToolsConfig = field(default_factory=FsToolsConfig)
    bash: BashToolsConfig = field(default_factory=BashToolsConfig)
    web: WebToolsConfig = field(default_factory=WebToolsConfig)


@dataclass
class PermissionsConfig:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    brigid: BrigidConfig = field(default_factory=BrigidConfig)
    models: dict[str, ModelProfile] = field(default_factory=dict)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    source_path: Path | None = None

    def profile_names(self) -> list[str]:
        return list(self.models.keys())

    def resolve(self, name: str) -> OllamaConfig | None:
        prof = self.models.get(name)
        if prof is None:
            return None
        return OllamaConfig(
            model=prof.model,
            host=prof.host or self.brigid.host,
            options=dict(prof.options),
            system_prompt=prof.system_prompt,
            tools=prof.tools,
        )

    def active(self) -> tuple[str, OllamaConfig]:
        name = self.brigid.default
        if name is None:
            name = next(iter(self.models), None)
        if name is None:
            return ("default", OllamaConfig())
        resolved = self.resolve(name)
        if resolved is None:
            raise ConfigError(f"active model {name!r} not found in profiles")
        return (name, resolved)


def _build_brigid(d: dict[str, Any]) -> BrigidConfig:
    return BrigidConfig(
        default=d.get("default"),
        host=d.get("host", BrigidConfig.host),
        personality=d.get("personality"),
    )


def _build_models(d: dict[str, Any]) -> dict[str, ModelProfile]:
    out: dict[str, ModelProfile] = {}
    for name, entry in d.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"[models.{name}] must be a table")
        out[name] = ModelProfile(
            name=name,
            model=entry.get("model", name),
            options=dict(entry.get("options", {})),
            system_prompt=entry.get("system_prompt"),
            host=entry.get("host"),
            tools=bool(entry.get("tools", True)),
        )
    return out


def _build_runtime(d: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig(
        max_steps_per_turn=int(d.get("max_steps_per_turn", RuntimeConfig.max_steps_per_turn)),
        persist_permissions=bool(d.get("persist_permissions", RuntimeConfig.persist_permissions)),
    )


def _build_tools(d: dict[str, Any]) -> ToolsConfig:
    fs_d = d.get("fs", {})
    bash_d = d.get("bash", {})
    web_d = d.get("web", {})
    return ToolsConfig(
        fs=FsToolsConfig(root=fs_d.get("root", FsToolsConfig.root)),
        bash=BashToolsConfig(
            timeout_seconds=int(bash_d.get("timeout_seconds", BashToolsConfig.timeout_seconds))
        ),
        web=WebToolsConfig(
            max_bytes=int(web_d.get("max_bytes", WebToolsConfig.max_bytes)),
            timeout_seconds=int(web_d.get("timeout_seconds", WebToolsConfig.timeout_seconds)),
        ),
    )


def _build_permissions(d: dict[str, Any]) -> PermissionsConfig:
    return PermissionsConfig(
        allow=list(d.get("allow", [])),
        deny=list(d.get("deny", [])),
    )


def _build_mcp_servers(servers: list[dict[str, Any]]) -> list[MCPServerConfig]:
    out: list[MCPServerConfig] = []
    for entry in servers:
        if "name" not in entry or "command" not in entry:
            raise ConfigError(f"mcp.servers entry missing name or command: {entry!r}")
        out.append(
            MCPServerConfig(
                name=entry["name"],
                command=entry["command"],
                args=list(entry.get("args", [])),
                env=dict(entry.get("env", {})),
            )
        )
    return out


def from_dict(raw: dict[str, Any]) -> Config:
    expanded = _expand_env_in(raw)
    mcp_block = expanded.get("mcp", {})
    brigid = _build_brigid(expanded.get("brigid", {}))
    models = _build_models(expanded.get("models", {}))
    if brigid.default is not None and brigid.default not in models:
        avail = ", ".join(models) or "(none)"
        raise ConfigError(
            f"[brigid].default = {brigid.default!r} is not a defined model; available: {avail}"
        )
    return Config(
        brigid=brigid,
        models=models,
        runtime=_build_runtime(expanded.get("runtime", {})),
        tools=_build_tools(expanded.get("tools", {})),
        permissions=_build_permissions(expanded.get("permissions", {})),
        mcp_servers=_build_mcp_servers(mcp_block.get("servers", [])),
    )


def load(path: Path | str | None = None) -> Config:
    """Load config from a TOML file. If path is None, use the default location.
    Returns a Config with sensible defaults if the file doesn't exist."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not p.exists():
        cfg = Config()
        cfg.source_path = p
        return cfg
    try:
        with p.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"failed to parse {p}: {e}") from e
    cfg = from_dict(raw)
    cfg.source_path = p
    return cfg
