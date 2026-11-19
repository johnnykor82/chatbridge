# Bug Assess — 2026-11-19

**Reproduction evidence:** request `f33c2ed2...` from 2026-11-18 21:01 and `5fca35e6...` from 2026-11-19 08:01 navigated successfully. ChatBridge showed no source POST / HTTP status / user ID for 30 minutes; first request ended `unknown`, second repeated `submitting`. The owner inspected authenticated ChatGPT project and found no corresponding evening conversation. No changes or retries were performed.

**Code cause candidate:** `BrowserTransport.generate` selects `get_by_role('button', name=^(Send|Отправить)$)` and falls back to `editor.press('Enter')` if label absent or disabled. A ChatGPT icon-only send button may be usable but not match the accessible-name filter; Enter can insert newline instead of submit. Code then waits for original POST for the FULL 1800s hard timeout, silently blocking the report. This is a plausible root cause, not proven yet; reproduce synthetic icon-only/Enter case and verify no POST before Fix.

**Risk:** a simple retry after click is unsafe because network POST can occur without visible evidence. Therefore do not automatically send twice. Preserve gateway source-bound POST evidence and engine's durable attempted intent. Stage durations and browser diagnostics are currently too coarse.
