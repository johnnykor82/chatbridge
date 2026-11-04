# CB-16 — PLAN v1.0 (owner approved 2026-11-04)
**Approval date:** 2026-11-04. **Scope:** fix model-picker selector timeout, publish ChatBridge update, install on JKMac-2, real marker test. No BF-23 autonomous subagent feature implementation.

**Prechange source backup:** `/Users/openclaw/AgentWorkspace/artifacts/backups/cb16-reasoning-picker-20261104/chatbridge-source.bundle` — verified by git bundle verify and SHA256. Base commit `4174615c5aa776aa3b2856ad2814686d36b68d11`.
**Rollback:** restore prior ChatBridge release and LaunchAgent from read-back verified host snapshot; do not revert/write live SQLite/profile unless integrity checks demand it.

| Step | Operation | Validation |
| --- | --- | --- |
| CB16-01 Assess | Inspect JKMac failing request, source, live read-only current model picker | confirmed high selected attribute; slider appears after click; prior failure before submission |
| CB16-02 TDD RED | Add focused UI-unit tests | current method fails unnecessary-menu and short-wait cases |
| CB16-03 Fix | COMPLETED: selected-level fast path and 18s mismatch slider wait | 7 focused tests GREEN; no guessed level |
| CB16-04 Regression | COMPLETED: 218 pytest PASS, Ruff critical PASS, beta.13 source/wheel SHA256 verified, source archive contains modified selector and standalone installer | all PASS |
| CB16-05 Backup | COMPLETED 2026-11-04: `/Users/jk/AgentWorkspace/artifacts/backups/cb16-jkmac-predeploy-20261104` (45 MB): full beta.12 immutable release, source, plist, installer marker, online state.db and browser sessions SQLite, release pointer. 7 SHA256 entries PASS, SQLite integrity OK, tarballs readable; user Chrome profile not modified. | rollback workable |
| CB16-06 Release | COMPLETED: public `v0.2.0-beta.13` at https://github.com/johnnykor82/chatbridge/releases/tag/v0.2.0-beta.13; source commit `f1d50c5bc20fb7ddca8a564a4355f4b4a7b4d79e`; wheel/sdist/SHA256 artifacts | exact tag and assets verified |
| CB16-07 Deploy | COMPLETED: JKMac-2 official ChatBridge installer upgrade with verified beta.13 tarball; active `0.2.0b13-21f327a77ef6`, `/ready` 200, health OK, SQLite integrity OK, browser profile preserved | rollback beta.12 backup remains readable |
| CB16-08 E2E | COMPLETED: one new persistent ChatBridge session and one message, completed answer `CB16_EXECUTOR_READY`, verified assistant message/conversation IDs. Watcher real production mapping fresh `cached=false`, `matches=1`, `evidence_verified=true`, exact URL https://chatgpt.com/c/6aeb8422-faec-83ed-bf53-1ea094c4c26d, 11s | PASS |
| CB16-09 Converge | COMPLETED: test/live evidence and full acceptance documented in CONVERGE.md; final Git docs commit recorded | PASS |

Owner expressly permits implementation/deployment of this bugfix. Never retry historical blocked Chrome chat send or previous failed request. Record result of each step here.
