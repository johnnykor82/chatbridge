# CB-16 — Architecture v1.0
**2026-11-04, before implementation.**

Modify ONLY `BrowserTransport._configure_reasoning_effort` in `src/chatbridge/browser_transport.py`. First ensure the real ProseMirror editor is visible (keep original hydration/reload behavior), locate native reasoning picker, inspect `data-selected-reasoning-effort`. If equals caller-requested `low/medium/high`, return without opening dropdown; a live observation on JKMac confirmed this DOM attribute reflects high. If missing/different, open model-picker via DOM click and wait 18 seconds for real slider; change with keyboard and verify `aria-valuenow`. After submenu closes, verify picker `data-selected-reasoning-effort` if present. Never send if proof absent. No direct ChatGPT API calls or credentials.

Provider generation, gateway API, queues, session model, idempotency and event handling remain unchanged. Live test must create an entirely new marker not printed into orchestrator conversation before search; use exact one matching chat and distinguish cached mapping from fresh provider evidence.

Rollback: restore verified prior ChatBridge release/plist, preserve existing database/profile. If a live model-picker failure persists, stop and do not retry the same request.
