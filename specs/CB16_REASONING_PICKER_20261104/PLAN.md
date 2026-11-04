# CB-16 — PLAN v1.0 (owner approved 2026-11-04)
**Approval date:** 2026-11-04. **Scope:** fix model-picker selector timeout, publish ChatBridge update, install on JKMac-2, real marker test. No BF-23 autonomous subagent feature implementation.

**Prechange source backup:** `/Users/openclaw/AgentWorkspace/artifacts/backups/cb16-reasoning-picker-20261104/chatbridge-source.bundle` — verified by git bundle verify and SHA256. Base commit `4174615c5aa776aa3b2856ad2814686d36b68d11`.
**Rollback:** restore prior ChatBridge release and LaunchAgent from read-back verified host snapshot; do not revert/write live SQLite/profile unless integrity checks demand it.

| Step | Operation | Validation |
| --- | --- | --- |
| CB16-01 Assess | Inspect JKMac failing request, source, live read-only current model picker | confirmed high selected attribute; slider appears after click; prior failure before submission |
| CB16-02 TDD RED | Add focused UI-unit tests | current method fails unnecessary-menu and short-wait cases |
| CB16-03 Fix | COMPLETED: selected-level fast path and 18s mismatch slider wait | 7 focused tests GREEN; no guessed level |
| CB16-04 Regression | Source full suite 218 PASS and Ruff critical PASS; packaging/version audit remains | exact release archive test pending |
| CB16-05 Backup | COMPLETED 2026-11-04: `/Users/jk/AgentWorkspace/artifacts/backups/cb16-jkmac-predeploy-20261104` (45 MB): full beta.12 immutable release, source, plist, installer marker, online state.db and browser sessions SQLite, release pointer. 7 SHA256 entries PASS, SQLite integrity OK, tarballs readable; user Chrome profile not modified. | rollback workable |
| CB16-06 Release | Git commit, public beta.13 release and publish assets | exact commit/tag/provenance |
| CB16-07 Deploy | JKMac only ChatBridge via official Mac Control-managed update, preserve Legacy; check health | PID, installed source, exact DB counts |
| CB16-08 E2E | New persistent ChatBridge chat; one new unique marker; verify completion; Watcher resolve-marker fresh HTTP | result and URL or explicit provider/search blocker |
| CB16-09 Converge | Requirements traced, docs and Git commit | no premature completion claim |

Owner expressly permits implementation/deployment of this bugfix. Never retry historical blocked Chrome chat send or previous failed request. Record result of each step here.
