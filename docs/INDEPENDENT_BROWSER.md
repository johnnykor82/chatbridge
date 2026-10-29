# Independent Chrome mode

## Scope and configuration

ChatBridge can own a Chrome process, a dedicated persistent user-data directory, and an automatically discovered loopback CDP port. It does not need Mac Control or Watcher as service dependencies.

Settings:
- CHATBRIDGE_BROWSER_MODE = managed or external. Clean installs default to managed. Existing installations explicitly setting CHATBRIDGE_CDP default to external.
- CHATBRIDGE_CHROME_PROFILE = dedicated profile location; default ~/.chatbridge/chrome-profile.
- CHATBRIDGE_CHROME_PATH = optional executable override; on macOS the normal Google Chrome application is used.
- CHATBRIDGE_CDP = explicit existing CDP URL for external mode; managed mode discovers its own dynamic port.
- The HTTP port and SQLite DB are controlled separately by CHATBRIDGE_PORT and CHATBRIDGE_DB.

Managed Chrome is launched with remote-debugging-port=0, user-data-dir pointing to its own profile and remote-debugging-address=127.0.0.1. The gateway reads DevToolsActivePort, verifies the browser WebSocket identity and holds an exclusive profile lock. Other Chrome profiles and processes are untouched.

After an unexpected gateway crash the new supervisor can adopt only a verifiably owned orphan browser, checking PID, original process start time, executable, user-data-dir, process group and CDP identity. After browser death it creates a new owned Chrome and dynamically updates the gateway transport. Retries use backoff.

## Service diagnostics

- GET /health is HTTP process liveness and can return 200 before browser login.
- GET /ready probes a browser tab and the ChatGPT composer. It returns 503 until Chrome and account authorization are actually usable.
- GET /v1/system/status reports managed/external mode, PID, CDP, restart count, capacity and status.
- The account is authorized only by logging into the dedicated Chrome window. Do not copy cookies or session tokens from Mac Control.

See [installation](INSTALL.md) for setup and a dedicated profile. A fresh login and cold reboot still require independent verification.
