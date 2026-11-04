# CB-16 — Tasks v1.0
**2026-11-04; owner approved plan version v1.0.**

- [x] CB16-01 Spec Kit read online, inspect failure, live model-picker DOM on JKMac, verify source backup.
- [x] CB16-01b Write SPEC, ARCHITECTURE, PLAN, TASKS and Analyze before implementation.
- [x] CB16-02 Three focused tests executed RED: 2 expected failures (unnecessary model-menu click despite selected high; 6000ms instead of 18000ms) and 1 negative-case PASS; no network.
- [x] CB16-03 Source fix: verify `data-selected-reasoning-effort` and skip dropdown when equal; wait 18s for slider if different; 7 focused tests GREEN.
- [ ] CB16-04 Full pytest regression 218 PASS, Ruff E4/E7/E9/F PASS; release archive version/tag and docs packaging pending.
- [x] CB16-05 Fresh JKMac service backup 45MB, 7 SHA256 checks PASS; online SQLite integrity PASS, release tarball readable; rollback to beta.12 preserved.
- [ ] CB16-06 Public ChatBridge beta.13 exact commit/tag/assets.
- [ ] CB16-07 Upgrade JKMac managed ChatBridge, verify health, previous profile/DB, Legacy unchanged.
- [ ] CB16-08 One new real first-message and fresh marker→URL test; no retry of failed historical request.
- [ ] CB16-09 Converge and final Git docs checkpoint.
