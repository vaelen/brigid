# Brigid

Local agentic chat harness backed by Ollama, with MCP server support and built-in tools (file ops, shell, web fetch).

## Quickstart

```bash
# 1. Install (uv handles env + deps)
uv sync

# 2. Make sure ollama is running and a model is pulled
ollama serve &           # if not already running
ollama pull qwen3.6:35b-a3b   # matches the "qwen" profile in the example config

# 3. Copy the example config
mkdir -p ~/.config/brigid
cp config.example.toml ~/.config/brigid/config.toml

# 4. Run
uv run brigid
```

## Slash commands

| Command                      | What it does                                                                                     |
|------------------------------|--------------------------------------------------------------------------------------------------|
| `/help`                      | List slash commands                                                                              |
| `/tools`                     | List available tools (built-in + MCP)                                                            |
| `/model [name]`              | List configured model profiles, or switch to one by name                                         |
| `/system [text\|clear]`      | Show, set, or clear the system prompt (effective on next turn)                                   |
| `/personality [name\|none]`  | Load a personality file as the system prompt, clear it (`none`), or list available personalities |
| `/clear`                     | Wipe the conversation history                                                                    |
| `/save <path>`               | Save the current session to a JSON file                                                          |
| `/load <path>`               | Load a session from a JSON file                                                                  |
| `/allow <pattern>`           | Add a permission allow pattern                                                                   |
| `/deny <pattern>`            | Add a permission deny pattern                                                                    |
| `/thinking on\|off`          | Show/hide model "thinking" output                                                                |
| `/exit`                      | Quit                                                                                             |

## Permissions

Every tool call is gated. The order is:

1. If the call matches a **deny** pattern → refused (model sees `[denied by policy]`).
2. Else if it matches an **allow** pattern → runs silently.
3. Else → interactive prompt: `[y]` once / `[Y]` always / `[n]` once / `[N]` always.

Patterns use `fnmatch` glob syntax over a per-tool key, e.g. `bash:git status*` or `fs.write:/Users/me/repos/**`.

## Quality gates

```bash
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
```
