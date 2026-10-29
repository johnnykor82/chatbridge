## 0.2.0b3 — 2026-10-29

- Limit the installer to explicit runtime files and use its managed lifecycle for legacy entry points.
- Reject cross-origin browser requests and DNS-rebinding Host headers while retaining keyless local API access.
- Clean release documentation and remove developer-specific operational notes.
- Independent clean-Mac installation, login and cold-reboot acceptance remain pending.

## 0.2.0b2 — 2026-10-28

- Interactive first-run installer offers to install missing uv instead of failing.
- Supports both Homebrew and the official standalone uv installer with explicit consent.
- Noninteractive runs never bootstrap dependencies; dry-run stays read-only.
- Added offline regression tests for absent uv, consent, refusal and failure cases.

# Changelog

## 0.2.0-alpha.1 — 2026-10-25 (experimental; deployed to the development Mac)

- Binary attachments through native JSON/base64 and multipart HTTP APIs.
- Generic file types, including audio, PDF, Word/Excel, archives, text and scripts.
- Each reply may contain multiple independent images and resource-card files.
- Original file bytes stored durably in SQLite and retrieved at `/v1/media/{id}`.
- SSE artifact events, explicit missing-artifact events and completeness flags.
- Configurable low/medium/high reasoning selection; default high.
- Effective effort now always persisted and returned by the API; idempotency
  rejects a reused request key when the effort level changes.
- Tests for multimedia input/output, type validation, binary integrity and idempotency.
- Provider acceptance of a given file type depends on account and model.

## 0.1.0 — 2026-10-24 (experimental)

- Initial async HTTP gateway for persistent ChatGPT web conversations.
- Browser CDP transport; browser-side fetch/SSE observation; final turn correlation.
- SQLite WAL sessions, requests, messages, event journal and approval audit.
- Ten-worker scheduler with per-conversation serialization and eleventh-request queue.
- OpenAI-style completions endpoint and resumable native SSE request events.
- History synchronization, idempotent client request IDs and restart reconciliation.
- Automatic MCP UI approval mechanism (conversation scope; once fallback).
- Live smoke and stress tests, GitHub CI, open-source packaging.

**Not yet release-certified:** real multi-tool MCP approval E2E and a >10-minute
tool-heavy run remain unverified. See docs/RELEASE_CHECKLIST.md for outstanding verification.
