# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from brigid.config import from_dict, load
from brigid.repl import _ActiveModel, _apply_startup_personality, _handle_slash

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
        self.assistant_label = "brigid"

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


def _cfg_with_personalities(tmp_path, **files):
    pdir = tmp_path / "personalities"
    pdir.mkdir()
    for name, body in files.items():
        (pdir / name).write_text(body)
    cfg = load(tmp_path / "config.toml")  # missing file -> defaults, source_path set
    # give it the same two profiles RAW defines, so /model works
    cfg.models = from_dict(RAW).models
    cfg.brigid = from_dict(RAW).brigid
    return cfg


async def test_personality_load_sets_prompt_and_marker(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    r = _FakeRenderer()
    cont = await _handle_slash("/personality luna", cfg, active, None, None, r)
    assert cont is True
    assert active.cfg.system_prompt == "You are Luna."
    assert active.personality == "luna"


async def test_personality_not_found_lists_available(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    r = _FakeRenderer()
    await _handle_slash("/personality ghost", cfg, active, None, None, r)
    assert active.personality is None
    out = "\n".join(r.console.lines)
    assert "luna" in out


async def test_personality_none_clears(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/personality none", cfg, active, None, None, _FakeRenderer())
    assert active.cfg.system_prompt is None
    assert active.personality is None


async def test_personality_no_arg_shows_current_and_lists(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.", atlas="You are Atlas.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    r = _FakeRenderer()
    await _handle_slash("/personality", cfg, active, None, None, r)
    out = "\n".join(r.console.lines)
    assert "luna" in out
    assert "atlas" in out
    assert "active personality" in out


async def test_personality_sticky_across_model_switch(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)  # starts on hermes (system_prompt "You are Hermes.")
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/model stheno", cfg, active, None, None, _FakeRenderer())
    assert active.name == "stheno"
    assert active.personality == "luna"
    assert active.cfg.system_prompt == "You are Luna."  # personality wins over profile


async def test_system_set_clears_personality_marker(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/system You are generic.", cfg, active, None, None, _FakeRenderer())
    assert active.personality is None
    assert active.cfg.system_prompt == "You are generic."


async def test_system_clear_clears_personality_marker(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    await _handle_slash("/system clear", cfg, active, None, None, _FakeRenderer())
    assert active.personality is None
    assert active.cfg.system_prompt is None


async def test_model_switch_clears_stale_personality_marker(tmp_path):
    """Fix 1 regression: if the personality file is gone, /model clears the marker."""
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    await _handle_slash("/personality luna", cfg, active, None, None, _FakeRenderer())
    assert active.personality == "luna"
    # delete the file so load_personality returns None
    (tmp_path / "personalities" / "luna").unlink()
    await _handle_slash("/model stheno", cfg, active, None, None, _FakeRenderer())
    assert active.name == "stheno"
    assert active.personality is None


async def test_startup_personality_applied(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    cfg.brigid = from_dict(
        {"brigid": {"default": "hermes", "personality": "luna"}, "models": RAW["models"]}
    ).brigid
    active = _active(cfg)
    r = _FakeRenderer()
    _apply_startup_personality(cfg, active, r.console)
    assert active.personality == "luna"
    assert active.cfg.system_prompt == "You are Luna."


async def test_startup_personality_missing_warns_and_continues(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    cfg.brigid = from_dict(
        {"brigid": {"default": "hermes", "personality": "ghost"}, "models": RAW["models"]}
    ).brigid
    active = _active(cfg)
    before = active.cfg.system_prompt
    r = _FakeRenderer()
    _apply_startup_personality(cfg, active, r.console)
    assert active.personality is None
    assert active.cfg.system_prompt == before  # unchanged
    out = "\n".join(r.console.lines)
    assert "ghost" in out


async def test_startup_personality_none_is_noop(tmp_path):
    cfg = _cfg_with_personalities(tmp_path, luna="You are Luna.")
    active = _active(cfg)
    before = active.cfg.system_prompt
    _apply_startup_personality(cfg, active, _FakeRenderer().console)
    assert active.personality is None
    assert active.cfg.system_prompt == before
