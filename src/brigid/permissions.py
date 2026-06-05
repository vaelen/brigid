# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Literal

from brigid.config import PermissionsConfig


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


@dataclass(frozen=True)
class Decision:
    """Static evaluation of a permission key against allow/deny lists."""

    outcome: Outcome
    matched_pattern: str | None = None


@dataclass(frozen=True)
class PromptOutcome:
    """Result of an interactive permission prompt."""

    allow: bool
    persist: tuple[Literal["allow", "deny"], str] | None = None


Prompter = Callable[[str], Awaitable[PromptOutcome]]


def evaluate(key: str, perms: PermissionsConfig) -> Decision:
    """Pure evaluation: deny patterns first, then allow patterns, else prompt."""
    for pat in perms.deny:
        if fnmatchcase(key, pat):
            return Decision(Outcome.DENY, pat)
    for pat in perms.allow:
        if fnmatchcase(key, pat):
            return Decision(Outcome.ALLOW, pat)
    return Decision(Outcome.PROMPT)


class PermissionGate:
    """Wraps a PermissionsConfig and an interactive prompter.

    Resolution order: deny → allow → prompt. The prompter may persist new
    patterns into the in-memory lists; callers can later flush these to disk.
    """

    def __init__(self, perms: PermissionsConfig, prompter: Prompter | None = None) -> None:
        self.perms = perms
        self.prompter = prompter

    async def check(self, key: str) -> bool:
        decision = evaluate(key, self.perms)
        if decision.outcome is Outcome.ALLOW:
            return True
        if decision.outcome is Outcome.DENY:
            return False
        if self.prompter is None:
            return False
        result = await self.prompter(key)
        if result.persist is not None:
            target, pattern = result.persist
            target_list = self.perms.allow if target == "allow" else self.perms.deny
            if pattern not in target_list:
                target_list.append(pattern)
        return result.allow
