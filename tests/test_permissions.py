# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from brigid.config import PermissionsConfig
from brigid.permissions import (
    Decision,
    Outcome,
    PermissionGate,
    PromptOutcome,
    evaluate,
)


def test_evaluate_deny_takes_precedence():
    perms = PermissionsConfig(allow=["bash:*"], deny=["bash:rm -rf *"])
    assert evaluate("bash:rm -rf /tmp", perms) == Decision(Outcome.DENY, "bash:rm -rf *")


def test_evaluate_allow_when_no_deny():
    perms = PermissionsConfig(allow=["fs.read:*"], deny=[])
    assert evaluate("fs.read:/tmp/foo", perms) == Decision(Outcome.ALLOW, "fs.read:*")


def test_evaluate_prompt_when_no_match():
    perms = PermissionsConfig(allow=["fs.read:*"], deny=["bash:rm *"])
    decision = evaluate("web.fetch:https://example.com", perms)
    assert decision == Decision(Outcome.PROMPT, None)


def test_evaluate_glob_specifics():
    perms = PermissionsConfig(allow=["bash:git status*", "bash:ls *"], deny=[])
    assert evaluate("bash:git status -sb", perms).outcome is Outcome.ALLOW
    assert evaluate("bash:git push", perms).outcome is Outcome.PROMPT


@pytest.mark.asyncio
async def test_gate_allow_path_runs_without_prompt():
    perms = PermissionsConfig(allow=["fs.read:*"], deny=[])
    called = False

    async def prompter(_key: str) -> PromptOutcome:
        nonlocal called
        called = True
        return PromptOutcome(allow=False)

    gate = PermissionGate(perms, prompter)
    assert await gate.check("fs.read:/x") is True
    assert called is False


@pytest.mark.asyncio
async def test_gate_deny_path_returns_false_without_prompt():
    perms = PermissionsConfig(allow=[], deny=["bash:sudo *"])
    gate = PermissionGate(perms, prompter=None)
    assert await gate.check("bash:sudo rm /") is False


@pytest.mark.asyncio
async def test_gate_prompt_no_prompter_denies():
    gate = PermissionGate(PermissionsConfig(), prompter=None)
    assert await gate.check("bash:anything") is False


@pytest.mark.asyncio
async def test_gate_prompter_persist_allow_appends():
    perms = PermissionsConfig()

    async def prompter(_key: str) -> PromptOutcome:
        return PromptOutcome(allow=True, persist=("allow", "bash:git *"))

    gate = PermissionGate(perms, prompter)
    assert await gate.check("bash:git status") is True
    assert "bash:git *" in perms.allow
    # second call with the same pattern should not duplicate
    assert await gate.check("bash:git diff") is True
    assert perms.allow.count("bash:git *") == 1


@pytest.mark.asyncio
async def test_gate_prompter_persist_deny_appends():
    perms = PermissionsConfig()

    async def prompter(_key: str) -> PromptOutcome:
        return PromptOutcome(allow=False, persist=("deny", "bash:rm *"))

    gate = PermissionGate(perms, prompter)
    assert await gate.check("bash:rm /tmp/x") is False
    assert perms.deny == ["bash:rm *"]


@pytest.mark.asyncio
async def test_gate_prompter_one_shot_no_persist():
    perms = PermissionsConfig()

    async def prompter(_key: str) -> PromptOutcome:
        return PromptOutcome(allow=True)

    gate = PermissionGate(perms, prompter)
    assert await gate.check("bash:echo hi") is True
    assert perms.allow == []
    assert perms.deny == []
