# CB33 BUG FIX — 2026-11-09
Assessed and verified root cause: concurrent context.expect_page waiters all capture the first Chrome page, proven with 2/3/5/10 mock CDP broadcasts. Fix in src/chatbridge/browser_transport.py:
- one instance-local _tab_creation_lock solely around CDP Target.createTarget -> verified Playwright Page; no lock around page.goto, submit or streaming.
- strict Target.getTargetInfo.targetId == newly allocated Target.createTarget.targetId; fail closed and close only our created CDP Target on identity mismatch/timeout/cancellation, no fallback.
- shielded creation to identify/cleanup allocated target when caller cancelled; weak Page -> targetId mapping; structured nonsecret request/session/target navigation and cleanup logs.
- preserve GatewayEngine Semaphore(10), per-session locking, existing 429 cooldown and no uncertain-send retries.
No API, schema, session, UI, packaged environment or installed service modified during fix stage.
