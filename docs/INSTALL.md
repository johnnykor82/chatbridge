# Install ChatBridge on macOS

ChatBridge is an **experimental, unofficial** local bridge to the ChatGPT web interface. It is not the official OpenAI API. Review [SECURITY.md](../SECURITY.md) before using it with connected MCP tools.

## Requirements

- macOS with an interactive desktop login. LaunchAgent starts **after user login**, not before FileVault unlock.
- Google Chrome installed in `/Applications/Google Chrome.app`. You can specify another executable with `--chrome-path`.
- `git` and a `python3` executable (Python 3.9+) to run the installer; [uv](https://docs.astral.sh/uv/getting-started/installation/) provisions isolated Python 3.13 for ChatBridge. **You do not need to install uv in advance.**
- If uv is missing, interactive installation **asks for consent** before running `brew install uv` (when Homebrew is available) or downloading and executing the [official uv standalone script](https://astral.sh/uv/install.sh) as the current user (when Homebrew is absent). The script is downloaded to a private temporary file, not piped from the network to a shell. The bootstrap does not edit your shell configuration. Nothing is installed if you decline; the installer prints manual instructions instead.
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

The installer launches a **new Chrome window with a private profile**. Sign in normally to ChatGPT in that window. It does not copy account tokens or cookies from another browser.

```sh
curl http://127.0.0.1:8787/health
curl -i http://127.0.0.1:8787/ready
curl http://127.0.0.1:8787/v1/system/status
```

`/health` means the gateway is online; it **does not** mean ChatGPT is authorized. `/ready` returns 503 before login and 200 after ChatGPT authentication and editor readiness.

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
