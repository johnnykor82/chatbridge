# CB-READY-01 — Explicit managed-browser authentication verification
Version 1.0 / 2026-11-03. Owner-requested repair.

## Facts / Assess
ChatBridge correctly reports HTTP 503 browser_idle from passive GET /ready
after a 300-second inactivity shutdown; chatgpt_authenticated is null, not
false. Mac Control wrongly classifies all 503 responses as login_required.
After explicit POST /v1/browser/wake, GET /ready returned HTTP 200,
chatgpt_authenticated=true on 2026-11-03. The account is logged in.

## Required behavior
- Preserve passive GET /ready and 300s browser idle shutdown.
- Add explicit authenticated probe POST /v1/browser/verify: for managed mode,
  acquire an activity lease, wake the owned browser only on request, and
  run the same real composer verification as GET /ready.
- Return actual ready/authenticated result; never assert auth from a cached
  closed browser, a generic /health, cookies, or a previous login.
- Preserve endpoint authentication, origin protection and error classifications.
  On wake failure give explicit 503 browser_unavailable; never mask as login.
- No unsolicited Chrome launch on passive status/health checks.
- Compatibility: existing GET /ready and POST /v1/browser/wake unchanged.
  API documentation and tests include authorization/idle/real readiness flow.
- Mac Control installer should use explicit verification only if passive
  /ready returns reason browser_idle; actual authentication failures remain
  login_required, and network errors remain unavailable.
- No writes to ChatGPT user content, no cookie extraction or profile migration.

## Acceptance
Red tests, focused tests, full repo regression, CI, spec Converge, Git commit.
Before rollout: readable owner-only backup of release, Chrome profile/SQLite,
LaunchAgent; after rollout: /health, /ready, 5-minute idle, explicit auth.
Rollback: restore original managed service release and plist, preserving data.
