# Multi-line input (Alt+Enter to submit)

**Date:** 2026-06-18
**Status:** Approved design, pending implementation

## Goal

Let the user type multi-line messages at the `you>` prompt. Today, Enter
submits immediately, so a message can only be a single line (paste with
embedded newlines is the only workaround).

## Behavior

- The main `you>` prompt becomes multi-line:
  - **Enter** inserts a newline.
  - **Alt+Enter** (equivalently **Esc** then **Enter**) submits the whole block
    as one user turn.
- The key hint is shown once in the **startup banner**, alongside the existing
  `/help for commands · Ctrl-D to quit` line, e.g.:
  `/help for commands · Alt+Enter to send, Enter for newline · Ctrl-D to quit`.
- Permission prompts (`permission>`, `pattern>`) are **unchanged** — single-line.

## Implementation

Single call site: `repl.py`, the main input read (currently `repl.py:85`).

```python
line = await psession.prompt_async(
    FormattedText([("class:prompt", "you> ")]),
    multiline=True,
)
```

- `multiline=True` is passed **per-call**, not on the `PromptSession`
  constructor, so the shared `psession` used by the permission/pattern prompts
  in `render.py` stays single-line (its `prompt_async` calls don't pass
  `multiline`, and the session default is off).

The hint is added to the banner in `_print_banner` (`repl.py:334`), extending
the existing `[dim]/help for commands · Ctrl-D to quit[/dim]` line with the
`Alt+Enter to send, Enter for newline` text.

No changes are needed elsewhere:
- `session.add_user(line)` already accepts the full string, so a multi-line
  block flows through as one user message.
- The `line.strip()` empty-input skip and the `stripped.startswith("/")`
  slash-command routing both operate on the full submitted text and keep
  working. (A leading `/` still routes to a slash command; slash commands are
  expected to be single-line, which is unaffected.)
- No config, `session.py`, or `render.py` prompter changes.

## Testing

- The REPL loop is interactive prompt_toolkit wiring and is not unit-testable
  without a pty. The change is two static edits (one kwarg, one banner string),
  so there is no new logic to unit-test.
- End-to-end behavior (Enter = newline, Alt+Enter = submit, banner hint visible,
  permission prompts still single-line) is verified manually / via
  `scripts/smoke.py`.

## Out of scope

- Toggling multi-line on/off, heredoc sentinels, or backslash continuation.
- Multi-line editing of slash-command arguments or permission patterns.
