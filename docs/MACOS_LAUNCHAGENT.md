# macOS LaunchAgent

Use [the standalone installer](INSTALL.md) for installation, upgrade, status,
diagnostics and removal. The compatibility scripts in `scripts/` delegate to
that installer and refuse to overwrite services it does not own.

The per-user LaunchAgent starts after graphical login and restarts after
crashes. Chrome uses a dedicated persistent profile and requires an interactive
ChatGPT sign-in. It does not start before login at the FileVault screen.

Default label: `net.chatbridge.gateway`; HTTP: `127.0.0.1:8787`;
state and logs: `~/.chatbridge/`. Use separate labels, state directories and
ports for additional instances. Never share a Chrome profile between processes.

```sh
./install.sh status
./install.sh doctor
./install.sh upgrade
./install.sh uninstall
```

Uninstall preserves account data unless explicit purge options are supplied.
Fresh-account sign-in and a cold reboot have not been certified.
