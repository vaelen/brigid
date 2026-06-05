# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from brigid import __version__
from brigid.config import load
from brigid.errors import BrigidError
from brigid.repl import run as run_repl


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="brigid",
        description="Local Ollama agent harness with MCP and built-in tools.",
    )
    p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to the TOML config (default: ~/.config/brigid/config.toml).",
    )
    p.add_argument("--version", action="version", version=f"brigid {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load(args.config)
    except BrigidError as e:
        print(f"brigid: config error: {e}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run_repl(cfg))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
