# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from brigid.config import from_dict
from brigid.repl import _ActiveModel, _handle_slash

RAW = {
    "brigid": {"default": "hermes"},
    "models": {
        "hermes": {
            "model": "hermes3-8b",
            "system_prompt": "You are Hermes.",
            "options": {"num_ctx": 32768},
        },
        "stheno": {"model": "stheno-8b", "options": {"num_ctx": 8192}},
    },
}


class _FakeConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))


class _FakeRenderer:
    def __init__(self) -> None:
        self.console = _FakeConsole()
        self.show_thinking = False

    def on_error(self, err: BaseException) -> None:
        self.console.print(f"error: {err}")


def _active(cfg):
    name, ocfg = cfg.active()
    return _ActiveModel(name, ocfg)


async def test_model_no_arg_lists_profiles():
    cfg = from_dict(RAW)
    active = _active(cfg)
    r = _FakeRenderer()
    cont = await _handle_slash("/model", cfg, active, None, None, r)
    assert cont is True
    out = "\n".join(r.console.lines)
    assert "hermes" in out
    assert "stheno" in out


async def test_model_switch_applies_all_fields():
    cfg = from_dict(RAW)
    active = _active(cfg)
    assert active.cfg.system_prompt == "You are Hermes."
    r = _FakeRenderer()
    await _handle_slash("/model stheno", cfg, active, None, None, r)
    assert active.name == "stheno"
    assert active.cfg.model == "stheno-8b"
    assert active.cfg.options["num_ctx"] == 8192
    assert active.cfg.system_prompt is None  # self-contained: cleared on switch


async def test_model_unknown_reports_available_and_no_change():
    cfg = from_dict(RAW)
    active = _active(cfg)
    before = (active.name, active.cfg.model)
    r = _FakeRenderer()
    await _handle_slash("/model nope", cfg, active, None, None, r)
    assert (active.name, active.cfg.model) == before
    out = "\n".join(r.console.lines)
    assert "hermes" in out
    assert "stheno" in out
