# CB34 Source Converge — 2026-11-19 (pre-deployment)
- **R1/R2 PASS:** native Send preflight supports visible/enabled icon-only or known named button, and no blind Enter. Login redirect and disabled controls fail before send_attempted; tests prove no click.
- **R3 PASS:** configurable 60-second source-bound original POST+2xx+user-ID receipt deadline, including repeated DOM errors; never auto-resends after a click. Prior 30-minute hard timeout applies to acknowledged model generation only.
- **R4 PASS:** privacy-safe cb34_stage, cb34_send_control, cb34_failure_stage, cb34_receipt_deadline structured log; no prompt, cookies, credentials, clinical content or screenshot in logs.
- **R5 PASS:** existing durable request IDs, attempt journal, HTTP 429 conflict checks, source-body match and restart tests remain passing; unknown provider turn is quarantined.
- **R6 PASS:** existing medical Telegram bot already sends one delay notice after 12 min, existing catch-up and idempotence checks pass (15 tests); ChatBridge reports unknown promptly instead of 30m.
- **R7 PASS:** 334/334 ChatBridge offline tests + 4 subtests, 15/15 medical tests, Ruff all src/tests/scripts and compileall.
- **R8 PENDING:** publish GitHub, verify source SHA/installer and both Mac launchd/health, controlled nonmedical end-to-end smoke, final Converge.
Rollback: Mac Server verified /Users/openclaw/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-macserver-20261119-103441; jkmac verified /Users/jk/AgentWorkspace/artifacts/backups/chatbridge-submit-fix-jkmac-20261119-103517. Old immutable releases remain. Source Converge **PASS**, end-to-end not yet proven.
