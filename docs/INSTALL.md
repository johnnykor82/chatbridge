# Install ChatBridge on macOS

ChatBridge is an **experimental, unofficial** local bridge to the ChatGPT web interface. It is not the official OpenAI API. Review [SECURITY.md](../SECURITY.md) before using it with connected MCP tools.

## Requirements

- macOS with an interactive desktop login. LaunchAgent starts **after user login**, not before FileVault unlock.
- Google Chrome installed in `/Applications/Google Chrome.app`. You can specify another executable with `--chrome-path`.
- `git` and a `python3` executable (Python 3.9+) to run the installer; [uv](https://docs.astral.sh/uv/getting-started/installation/) provisions isolated Python 3.13 for ChatBridge. **You do not need to install uv in advance.**
- The standard installation supports Apple Silicon. The installer checks the machine architecture and chooses a matching uv binary; Homebrew is not required or used.
- If uv is missing, interactive installation **asks for consent** before downloading uv as the current user. It first tries the [official Astral standalone installer](https://astral.sh/uv/install.sh). If `astral.sh` is unavailable, it automatically falls back to a checksum-verified official uv release hosted on GitHub. The binary is installed in a ChatBridge-managed directory; the bootstrap does not edit your shell configuration or build Rust/LLVM. If both sources are unavailable, the installer reports that uv could not be downloaded and prints manual installation instructions. Nothing is installed if you decline.
- Noninteractive installs never silently provision uv; install it yourself first. `--dry-run` does not install or prompt for any dependencies.

**Mac Control, Watcher, an existing CDP browser and manually managed Python environments are not required.**

## Install from GitHub

From the public GitHub repository:

```sh
git clone https://github.com/johnnykor82/chatbridge.git chatbridge
cd chatbridge
./install.sh
```

The installed service runs independently of the Git checkout directory.

Default locations:

| Location | Content |
|---|---|
| `~/.local/share/chatbridge/releases/` | Immutable application releases, each with its own Python 3.13 environment |
| `~/.chatbridge/state.db` | Persistent sessions, requests, events and downloaded files |
| `~/.chatbridge/chrome-profile/` | Dedicated Chrome account profile |
| `~/.chatbridge/service.stderr.log` | Service log |
| `~/.chatbridge/installer-backups/` | Property-list backups for upgrades |
| `~/Library/LaunchAgents/net.chatbridge.gateway.plist` | Per-user supervised macOS service |

The API binds to **127.0.0.1:8787**, concurrency defaults to 10 and reasoning effort to **High**. Managed Chrome chooses a free loopback CDP port automatically; it never uses the Mac Control browser or port 9335.

### First ChatGPT login

Chrome starts **in the background**. The installer waits for the managed browser and checks `/ready`; if the saved session is confirmed, it does not ask you to log in again. If the check is inconclusive, an interactive Terminal installation offers to open the managed profile to inspect the session. Answer `y` or `yes` and sign in only if needed. A network or browser failure can also make the check inconclusive. The installer checks that the browser is running, visible, and using the expected profile before offering and again after consent. Opening a window does not verify authentication; check `/ready` afterward.

Declining, cancelling the prompt, or installing without an interactive terminal preserves the installation and printed manual command. `--dry-run` and `--no-start` never offer a login window; an already ready service needs no prompt. A window-opening failure also leaves the installed service in place. When Mac Control invokes this installer in Terminal, the same prompt is available through its inherited terminal input/output.

For later manual login, first call `POST /v1/browser/wake`, then check `curl http://127.0.0.1:8787/v1/system/status` and wait until `browser.mode` is `managed` and `browser.state` is `running`. HTTP `/health` alone is insufficient: Chrome may still be starting. Do not run the window-opening command before this check, as it could race the supervisor for ownership of the profile. Then execute the command printed by the installer in Terminal and sign in normally to ChatGPT. For the default Chrome application and state directory, the command is:

```sh
curl -fsS -X POST http://127.0.0.1:8787/v1/browser/wake
# Wait for browser.mode=managed and browser.state=running in /v1/system/status.
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$HOME/.chatbridge/chrome-profile" \
  --new-window "https://chatgpt.com/"
```

This asks the already running Chrome for that exact profile to open a window. It does not copy cookies or account tokens from another browser. Reuse the command whenever login is needed again. Do not use an ordinary Chrome window belonging to a different profile.

For custom `--state-dir` or `--chrome-path`, use the shell-quoted command printed by the installer, or the `login_command` value from `./install.sh doctor` (with `--label YOUR_LABEL` for a custom service). Doctor reads the installed paths, including after an upgrade; copy the decoded command value, without JSON quotation/escaping. No API key is included in this command. If API authentication is enabled, use your existing authenticated client for the wake request and status check (add `Authorization: Bearer <your-API-key>` to the curl requests); the browser-opening command itself needs no token. When Chrome is running, `/ready` creates and closes its own temporary probe tab; it is not a persistent login window.

```sh
curl http://127.0.0.1:8787/health
curl -i http://127.0.0.1:8787/ready
curl http://127.0.0.1:8787/v1/system/status
```

`/health` means the gateway is online; it **does not** mean ChatGPT is authorized. `/ready` returns 503 before login or while Chrome is idle, and 200 after ChatGPT authentication and editor readiness when Chrome is running.

After five minutes without real browser operations, ChatBridge stops its owned Chrome and reports `browser.state=idle`, preserving the profile. A new browser operation starts Chrome and waits for it. `/health`, `/v1/system/status` and `/ready` do not wake Chrome. In idle, `/ready` returns HTTP 503 with `ready=false`, `chatgpt_authenticated=null` and `reason=browser_idle`. This also applies to a window opened manually in the managed profile: it can close on idle. Call `POST /v1/browser/wake`, wait for `running`, and then use the login command again when needed.

## Commands

```sh
./install.sh status
./install.sh doctor
./install.sh upgrade
./install.sh uninstall
```

`status` and `doctor` show configuration and readiness without printing API keys. By default **uninstall preserves the browser profile, database, logs and installed releases**.

```sh
./install.sh uninstall --remove-app
./install.sh uninstall --remove-app --purge-data --yes
```

The final command permanently deletes local ChatBridge account profile and history. Data removal requires `--yes` and an installer-owned state directory. Back up your files first.

### Alternate label, port and data location

Useful for testing beside another installation:

```sh
./install.sh install \
  --label net.chatbridge.gateway.dev \
  --port 18887 \
  --prefix "$HOME/.local/share/chatbridge-dev" \
  --state-dir "$HOME/.chatbridge-dev"
```

Later:

```sh
./install.sh upgrade --label net.chatbridge.gateway.dev
./install.sh doctor --label net.chatbridge.gateway.dev
./install.sh uninstall --label net.chatbridge.gateway.dev
```

Upgrades **retain the previous port, Chrome executable, data location, API key, concurrency and reasoning-effort setting**, unless a different value is explicitly supplied.

The `--no-start` option builds the application and writes a LaunchAgent without starting it. `--dry-run` prints the installation plan without changing the system.

For a service installed with `--no-start`, activate it with:

```sh
launchctl bootstrap gui/$(id -u) \
  "$HOME/Library/LaunchAgents/net.chatbridge.gateway.plist"
```

## Upgrade and rollback

Pull the new source and switch releases:

```sh
git pull --ff-only
./install.sh upgrade
```

The installer builds a separate **immutable release**, pins dependencies with `uv --locked`, verifies installed Python package imports and **only then** updates the LaunchAgent. The previous plist is backed up. If the service does not start or pass `/health`, the previous LaunchAgent is restored and loaded again. Previous release directories and the persistent SQLite/Chrome profile remain untouched.

Upgrade when the queue is idle. If the gateway is interrupted during an active ChatGPT request, that request may be marked `unknown`; it must be reconciled rather than blindly resent.

**Existing manually managed services are protected.** An existing LaunchAgent without this installer's marker will not be overwritten. If you already run another ChatBridge instance, use a different `--label`, port and state directory, or plan a migration explicitly.

## Operation

```sh
launchctl print gui/$(id -u)/net.chatbridge.gateway
launchctl kickstart -k gui/$(id -u)/net.chatbridge.gateway
tail -f ~/.chatbridge/service.stderr.log
```

LaunchAgent settings include `RunAtLoad=true`, `KeepAlive=true` and a 15-second restart throttle. ChatBridge independently restarts its own Chrome after browser crashes; after a gateway crash, the new gateway can reconnect only to a verified browser it previously owned. It refuses to take over another Chrome process.

`/ready` can report unavailable if Chrome is not logged in, ChatGPT imposes account restrictions, or the web interface changes. Do not repeatedly resubmit uncertain requests.

Image, audio, document and binary file support is described in [Attachments](ATTACHMENTS.md). Stored media occupies SQLite space; monitor disk usage and back up `state.db`.

## Security and scope

- This API is local by default. Do not expose it on a public interface without deliberate authorization and network controls.
- For non-loopback binding, set `CHATBRIDGE_API_KEY` in the environment before installation and explicitly supply `--host`. An API key is mandatory. Avoid placing secret keys in shell-history arguments.
- Never publish Chrome account profiles, SQLite databases, credentials, backups, private logs or user data to GitHub.
- Automatic approval of some ChatGPT MCP/tool prompts may grant an AI agent broad control over connected tools. Read [SECURITY.md](../SECURITY.md) and review the configured approval behavior before connecting sensitive systems.
- Provider support for file formats, models, thinking levels, tools and limits varies by account; the web interface may change without notice.

## Acceptance status

Installer packaging with uv/Python 3.13, separate app/data locations, update with preserved configuration, offline HTTP test, LaunchAgent startup and restart after SIGKILL were checked on a Mac with a **test service label and mock transport**. The development gateway and Watcher were not overwritten.

A completely fresh macOS account, first real ChatGPT login, empty dependency cache, true cold boot and extensive live MCP execution remain release-acceptance checks. Do not claim those have passed.
