# CB-READY-01 — plan
Version 1.0 / 2026-11-03. Authorized by owner's request to find ChatBridge
repository, fix the login/readiness issue, commit, and update installed service.
Changes to Mac Control installer are a dependent compatibility step; keep
that deployment gated on its own suite and verified release backup.

## Verified recovery backup
/Users/openclaw/AgentWorkspace/artifacts/backups/chatbridge-idle-readiness-20261103/chatbridge-installed-before.tar.gz
SHA256 9c63d21dbadeb3b2b7b180faf34370d5cee345a59b4957709d80a5aeb254212e
152 MiB; gzip -t and tar -tzf PASS. Includes managed browser profile,
SQLite, installed source/release and owned LaunchAgent plist. Store 0700,
file 0600. Restore only with service stopped, then restart original LaunchAgent.

## Architecture / Analyze
Existing GET /ready is deliberately passive for 300-second idle close.
Existing POST /v1/browser/wake opens browser but does not verify login.
Add POST /v1/browser/verify with a ChromeSupervisor.activity() lease and
call the same readiness function after waking. Fast failure remains 503,
success requires live ChatGPT composer. Guard by existing auth and
cross-origin middleware. Static OpenAPI describes endpoint.
Mac Control installer can check passive reason then explicitly verify once;
avoid automatic polling wake loops. Never replace login with inferred truth.
Canonical repo: https://github.com/johnnykor82/chatbridge (clean clone
/Users/openclaw/AgentWorkspace/projects/chatbridge), source SHA 05fdfa3;
old dirty checkout /Users/openclaw/chatgpt-mcp/workspace/release-publication-20261028/chatbridge untouched.

## Tasks
- [x] 0. Review latest GitHub Spec Kit and Bug Fix process; diagnose passive idle.
- [x] 1. Find canonical source, prove owner auth by wake + live ready, backup and verify.
- [x] 2. Write red tests for explicit probe and guards.
- [x] 3. Implement route, documentation and tests.
- [x] 4. Full suite, Analyze/Converge, Git commit and push feature branch.
- [x] 5. Install immutable ChatBridge release using owned installer, verify,
      rollback on failure; do not erase profile or sessions.
- [ ] 6. Update Mac Control client compatibility and deploy only after separate
      source tests/CI, backup/rollback validation.
No steps outside this documented scope without owner authorization.


## Converge 2026-11-03 — source only
- Verified original managed profile login from live ChatBridge: GET /ready
  idle 503 browser_idle, POST /v1/browser/wake 200, then GET /ready 200
  chatgpt_authenticated true. User was logged in; the fault was readiness
  classification/idle integration, not lost authentication.
- New explicit POST /v1/browser/verify leases browser for full wake+composer
  probe, returns real successful or failed auth, does not affect passive GET
  /ready, and inherits bearer/Origin checks. Direct idle + unauthorized
  tests, OpenAPI schema and doctor diagnostics all covered.
- Test-first: 2 expected red tests (404 missing endpoint, missing schema).
  After fixing test event reuse, 2 passed. Idle+doctor suite 32 PASS.
  Full isolated ChatBridge suite 215 PASS. Ruff PASS, Python compilation
  PASS, OpenAPI JSON parse PASS, git diff --check PASS.
- Immutable candidate release tag: 0.2.0b11-c04804c3f2e5.
- No database/profile migration, automatic login, cookies or unintended
  browser wake in status polling. Existing saved managed profile preserved.
- Source Converge: PASS. Deployment/install smoke and Mac Control client's
  idle detection are separate outstanding rollout gates.


## Deployment Converge — 2026-11-03
- Immutable installed release 0.2.0b11-c04804c3f2e5 via original owned
  ChatBridge installer upgrade, no profile/SQLite wipe or cookie migration.
  LaunchAgent net.chatbridge.gateway.maccontrol running.
- Live HTTP: /health 200, /v1/system/status 200,
  /v1/browser/verify POST 200 (chatgpt_authenticated:true), GET /ready
  200 (authenticated:true). Existing sessions still 4. Release server.py
  SHA256 identical to tested source:
  2d12940fd25d5d478d8c63e20d53a410c264933af7d106fc096431d8b488ef55.
- SQLite remains /Users/openclaw/MacControlMCP/deps/chatbridge-data/state.db;
  Chrome profile unchanged under chatbridge-data/chrome-profile.
- GitHub branch feature/idle-ready-verify-20261103 commit 02e15bd;
  215 tests PASS, Ruff PASS, no CI workflows configured in this repo.
- ChatBridge Converge PASS. Mac Control installer still needs FR024 client
  adaptation to use explicit verify only when browser_idle.
