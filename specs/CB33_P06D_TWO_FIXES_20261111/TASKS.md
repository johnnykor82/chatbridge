# CB33 P06D Tasks v1.2 — 2026-11-11

- [x] F01 official online Spec Kit; baseline/source review; backed up both live DB and service plists; exact installed Watcher base; isolated worktree.
- [x] F02 SPEC/ARCH/PLAN/TASKS/ANALYZE and two Bug Assess cases consistent; approved v1.2 scope and verified rollback recorded before code edits.
- [x] F03 ChatBridge RED: 2 expected failures (first slider timeout never reopens, persistent missing lacks second bounded attempt), 1 already-selected control PASS, offline.
- [x] F03 Watcher RED: 4 expected failures (standard reuse, ambiguous session, mismatched project and forbidden mode), all in-memory SQLite; no live Send.
- [x] F04 ChatBridge pre-Send one bounded menu reopen + strict slider verification; 6 targeted GREEN; BUG_FIX updated.
- [x] F04 Watcher exact-route local standard-session reuse, mode/mismatch/ambiguity fail-closed; 4 targeted GREEN; BUG_FIX updated.
- [ ] F05 full offline tests complete: Bridge 327 +4 PASS; Watcher 895 +1151 PASS, 1 SKIP, security 4 PASS; Ruff/compile/diff PASS; source Converge PARTIAL due P08 runtime baseline, Git commits pending.
- [ ] F05/F06 blocker: actual watcher runtime sources P08 d978bcd via LaunchAgent PYTHONPATH rather than 749a748; require owner-approved amended P08 worktree/deploy before rollout. Do not deploy 749 prototype.
- [ ] F06 fresh backup reverify and safe owner-only deployment, rollback and health checks.
- [ ] F07 resolve existing B READY event based on durable receipt and ACK only if delivered.
- [ ] F08 exactly two new independent workers with verified overlap/SHA/ACK/goal review; no historical retry.
- [ ] F09 original BF33 unchanged across 11 Watcher tables, final Converge, docs/backlog/test log and Git commit.
