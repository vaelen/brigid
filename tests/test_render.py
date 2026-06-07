# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import io

from rich.console import Console

from brigid.render import RichRenderer


def _renderer():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    return RichRenderer(console=console), buf


def test_assistant_label_defaults_to_brigid():
    r, buf = _renderer()
    r.on_assistant_chunk("hello")
    assert buf.getvalue().startswith("brigid: ")


def test_assistant_label_reflects_personality():
    r, buf = _renderer()
    r.assistant_label = "luna"
    r.on_assistant_chunk("hello")
    assert buf.getvalue().startswith("luna: ")
