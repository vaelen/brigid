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
