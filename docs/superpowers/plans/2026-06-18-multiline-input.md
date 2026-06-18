# Multi-line Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user type multi-line messages at the `you>` prompt, submitting with Alt+Enter.

**Architecture:** Pass `multiline=True` per-call on the main input read in `repl.py` (prompt_toolkit then maps Enter→newline, Alt+Enter→submit). Surface the keybinding once in the startup banner. No other modules change.

**Tech Stack:** Python 3.11, prompt_toolkit, rich.

## Global Constraints

- All modules start with `from __future__ import annotations`; target Python 3.11. (No new module here, so nothing to add.)
- `multiline=True` is passed **per-call**, never on the `PromptSession` constructor — the shared `psession` is also used by `render.py`'s permission/pattern prompts, which must stay single-line.
- Verify with `uv run ruff format`, `uv run ruff check`, `uv run pyright`, `uv run pytest`.

---

### Task 1: Multi-line `you>` prompt + banner hint

**Files:**
- Modify: `src/brigid/repl.py` (the main input read, currently line ~85, and `_print_banner`, currently line ~334)

**Interfaces:**
- Consumes: existing `psession.prompt_async(...)` call and `_print_banner(console, active, registry)`.
- Produces: no new symbols; behavior change only.

- [ ] **Step 1: Add `multiline=True` to the main input read**

In `run()`, change the main prompt call from:

```python
            try:
                line = await psession.prompt_async(FormattedText([("class:prompt", "you> ")]))
```

to:

```python
            try:
                line = await psession.prompt_async(
                    FormattedText([("class:prompt", "you> ")]),
                    multiline=True,
                )
```

Leave the surrounding `try/except (EOFError, KeyboardInterrupt)` and the
`stripped = line.strip()` / slash-command routing below it untouched — they
operate on the full submitted text and already handle multi-line input.

- [ ] **Step 2: Add the keybinding hint to the startup banner**

In `_print_banner`, change:

```python
    console.print("[dim]/help for commands · Ctrl-D to quit[/dim]")
```

to:

```python
    console.print(
        "[dim]/help for commands · Alt+Enter to send, Enter for newline · "
        "Ctrl-D to quit[/dim]"
    )
```

- [ ] **Step 3: Lint, type-check, and test**

Run:

```bash
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
```

Expected: ruff reports no changes/issues, pyright passes, full suite passes
(the change is interactive prompt_toolkit wiring with no new logic to unit-test;
the existing suite must remain green).

- [ ] **Step 4: Manual smoke check (optional, requires a TTY)**

Run `uv run brigid`. Confirm:
- The banner shows `Alt+Enter to send, Enter for newline`.
- At `you>`, **Enter** inserts a newline; **Alt+Enter** (or Esc then Enter) submits.
- Triggering a permission prompt still shows a single-line `permission>` prompt.

- [ ] **Step 5: Commit**

```bash
git add src/brigid/repl.py
git commit -m "feat(repl): multi-line input with Alt+Enter to submit"
```

---

## Self-Review

- **Spec coverage:** multi-line prompt (Step 1), Alt+Enter submit semantics (prompt_toolkit `multiline=True`, Step 1), banner hint (Step 2), permission prompts unchanged (per-call `multiline`, Global Constraints + Step 4), no config/session/render-prompter changes (only `repl.py` touched). All spec points covered.
- **Placeholders:** none — both edits show full before/after code.
- **Type consistency:** no new symbols introduced; signatures unchanged.
