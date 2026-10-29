[**English**](README.md) · [Русский](README.ru.md)

# ChatBridge

**Turn your authorized ChatGPT web session into a persistent local HTTP API for AI agents.**

ChatBridge is an independent, unofficial gateway. It manages its own Chrome profile, accesses your signed-in ChatGPT conversations, preserves messages and attachments, and exposes request status, streaming events and history over HTTP. Clients do not need to automate a browser.

> **Independent module.** ChatBridge works on its own. It can also work with **Mac Control**, a separate MCP server giving AI agents capabilities to interact with a Mac. Neither component is required to install the other.

**Status:** experimental preview, tested locally and in real workflows. This is **not the official OpenAI API**. ChatGPT website changes and account limits can affect it.

## Main capabilities

- **Conversations:** create sessions, reuse existing ChatGPT discussions, sync message history.
- **HTTP + SSE:** asynchronous requests, persistent IDs, resumable ordered events and tool-progress tracking.
- **Multimodal files:** upload images, audio, PDF, Word, Excel, scripts and other binary files; save and download all available response attachments, including **multiple images or documents per response**.
- **Thinking effort:** `low`, `medium` or `high` on each request; **High by default**, with UI selection verification.
- **Queue and concurrency:** 10 active sessions by default, with per-conversation serialization.
- **Recovery:** SQLite persistence, no blind retries on uncertain sends, managed Chrome startup and crash recovery.
- **Background Browser API v1:** ChatBridge owns Chrome/CDP and exposes isolated browser sessions, navigation, DOM, click/fill, screenshots and file endpoints for external authorized clients. Visible-background mode avoids taking focus where supported.
- **MCP-compatible workflows:** ChatGPT may use connected MCP tools; configurable automation of tool approvals.

## Optional integration: Mac Control

**Mac Control is a separate project, not a dependency of ChatBridge.** The two modules solve different problems:

| ChatBridge | Mac Control |
|---|---|
| Connects external agents to ChatGPT through HTTP | Exposes macOS capabilities to agents via MCP |
| Stores sessions, message history and response files | Supports permitted terminal commands and filesystem operations |
| Owns its authenticated Chrome and request lifecycle | Provides system inspection, service operations and macOS workflow automation |
| Returns ChatGPT answers and artifacts to clients | Can be invoked by ChatGPT as an optional tool service |

For example, an application sends work to ChatBridge; ChatGPT can use Mac Control to inspect or modify approved resources on the Mac, then ChatBridge delivers the response and files back to the application.

**Mac Control repository:** [johnnykor82/mac-control-mcp-server](https://github.com/johnnykor82/mac-control-mcp-server).

## Install on macOS

Installation requires macOS, Google Chrome, Git and Python 3. The installer checks the Mac architecture and selects a matching uv binary. If [uv](https://docs.astral.sh/uv/) is missing, it asks permission to download uv from the official Astral installer; if `astral.sh` is unavailable, it automatically tries a checksum-verified official uv release from GitHub. Homebrew is not required or used. uv is installed in a ChatBridge-managed directory without changing shell configuration, then provisions an isolated Python 3.13 runtime. If both download sources are unavailable, the installer reports the error and provides manual installation instructions.

```bash
git clone https://github.com/johnnykor82/chatbridge.git
cd chatbridge
./install.sh
```

The installer creates an independent application directory, private Chrome profile and per-user LaunchAgent with automatic startup and restart. Chrome starts **in the background**. The installer waits for the browser and checks the saved ChatGPT session. If it cannot confirm the session, it offers to open the managed profile (`[y/N]`); inspect the session and sign in manually only if needed. Network or browser issues can also make the check inconclusive. Declining or installing without a terminal leaves the service installed and prints a command for later login. Before running that command (also available from `./install.sh doctor`), call `POST /v1/browser/wake`, then wait until `/v1/system/status` shows `browser.mode=managed` and `browser.state=running`. Use your configured API key for wake and status when authentication is enabled. Mac Control and a pre-existing CDP browser are not needed.

After five minutes without real browser operations, ChatBridge stops its owned Chrome and reports `browser.state=idle`; the profile is preserved. A new browser operation starts Chrome and waits for it. `/health`, `/v1/system/status` and `/ready` do not wake it. While idle, `/ready` returns HTTP 503 with `ready=false`, `chatgpt_authenticated=null` and `reason=browser_idle`. A manually opened window in the managed profile can also close when Chrome becomes idle; use `POST /v1/browser/wake` before reopening it for login.

Default API: **http://127.0.0.1:8787**.

```bash
./install.sh status      # Installation information
./install.sh doctor      # Health and login check
./install.sh upgrade     # New release with rollback support
./install.sh uninstall   # Remove service; keep user data
```

Full instructions: [Installation and upgrades](docs/INSTALL.md).

## HTTP API quick start

```bash
# Create a persistent session
curl -s -X POST http://127.0.0.1:8787/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"my-agent"}'

# Use the session ID from the response
curl -s -X POST http://127.0.0.1:8787/v1/sessions/SESSION_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"Analyze the architecture","reasoning_effort":"high"}'
```

Read `GET /v1/requests/REQUEST_ID` to fetch status and the final answer, or `GET /v1/requests/REQUEST_ID/events` to stream SSE. Download attachments through `GET /v1/media/MEDIA_ID`.

There is a **partial** OpenAI-style `/v1/chat/completions` endpoint. It is not fully OpenAI API-compatible; its SSE stream may return the final answer in one chunk.

Documentation: [API reference](docs/API_REFERENCE.md) · [All file types](docs/ATTACHMENTS.md) · [Image handling](docs/IMAGES.md).

## Security and limitations

ChatBridge may automatically approve ChatGPT MCP tool requests depending on its configuration. Such tools can run consequential operations with the user's permissions. Read [Security and autonomy](SECURITY.md) before enabling privileged MCP services.

ChatBridge is based on the authorized ChatGPT web interface, which can change. Thinking settings, model access, supported files and limits vary by account. Clean-machine installation and high-concurrency stress testing have not yet been fully certified. Avoid exposing the local API to untrusted networks.

## Development

```bash
uv sync --extra dev --locked
uv run ruff check src tests scripts/installer.py
uv run pytest -q
uv build
```

Licensed under [MIT](LICENSE). The code is independently implemented.

---

[Русская версия](README.ru.md)
