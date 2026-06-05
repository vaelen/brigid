# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from brigid.config import OllamaConfig
from brigid.llm import OllamaBackend


def _backend(system_prompt: str | None) -> OllamaBackend:
    cfg = OllamaConfig(system_prompt=system_prompt)
    # We never actually call .stream() in these tests; pass a stub client to
    # avoid a real httpx connection being constructed.
    return OllamaBackend(cfg, client=object())  # type: ignore[arg-type]


def test_no_config_no_leading_system_returns_unchanged():
    b = _backend(None)
    msgs = [{"role": "user", "content": "hi"}]
    assert b._with_system_prompt(msgs) is msgs


def test_no_config_with_leading_system_preserves_existing():
    b = _backend(None)
    msgs = [
        {"role": "system", "content": "from a loaded session"},
        {"role": "user", "content": "hi"},
    ]
    assert b._with_system_prompt(msgs) is msgs


def test_config_set_no_leading_system_prepends():
    b = _backend("be terse")
    msgs = [{"role": "user", "content": "hi"}]
    out = b._with_system_prompt(msgs)
    assert out[0] == {"role": "system", "content": "be terse"}
    assert out[1:] == msgs


def test_config_set_with_leading_system_replaces_head_only():
    b = _backend("be terse")
    msgs = [
        {"role": "system", "content": "old prompt"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first reply"},
        {"role": "user", "content": "second"},
    ]
    out = b._with_system_prompt(msgs)
    assert out[0] == {"role": "system", "content": "be terse"}
    # Tail (user/assistant turns) is untouched.
    assert out[1:] == msgs[1:]
    # Original list is not mutated.
    assert msgs[0] == {"role": "system", "content": "old prompt"}


def test_client_rebuilt_when_host_changes():
    cfg = OllamaConfig(host="http://a:11434")
    sentinel = object()
    b = OllamaBackend(cfg, client=sentinel)  # type: ignore[arg-type]
    assert b.client is sentinel
    cfg.host = "http://b:11434"
    b._ensure_client()
    assert b.client is not sentinel
    assert b._client_host == "http://b:11434"


def test_client_not_rebuilt_when_host_unchanged():
    cfg = OllamaConfig(host="http://a:11434")
    sentinel = object()
    b = OllamaBackend(cfg, client=sentinel)  # type: ignore[arg-type]
    b._ensure_client()
    assert b.client is sentinel
    assert b._client_host == "http://a:11434"


class _RecordingClient:
    """Fake AsyncClient that records the `tools` kwarg from chat()."""

    def __init__(self) -> None:
        self.last_tools: object = "unset"

    async def chat(self, **kwargs):
        self.last_tools = kwargs.get("tools")

        async def _gen():
            return
            yield  # pragma: no cover  (makes this an async generator)

        return _gen()


async def _drain(backend, schemas):
    async for _ in backend.stream([{"role": "user", "content": "hi"}], schemas):
        pass


_SCHEMAS = [{"type": "function", "function": {"name": "x"}}]


async def test_stream_passes_tools_when_enabled():
    cfg = OllamaConfig(tools=True)
    fake = _RecordingClient()
    b = OllamaBackend(cfg, client=fake)  # type: ignore[arg-type]
    await _drain(b, _SCHEMAS)
    assert fake.last_tools == _SCHEMAS


async def test_stream_suppresses_tools_when_disabled():
    cfg = OllamaConfig(tools=False)
    fake = _RecordingClient()
    b = OllamaBackend(cfg, client=fake)  # type: ignore[arg-type]
    await _drain(b, _SCHEMAS)
    assert fake.last_tools is None
