# CB-16 — Tasks v1.0
**2026-11-04; owner approved plan version v1.0.**

- [x] CB16-01 Spec Kit read online, inspect failure, live model-picker DOM on JKMac, verify source backup.
- [x] CB16-01b Write SPEC, ARCHITECTURE, PLAN, TASKS and Analyze before implementation.
- [x] CB16-02 Three focused tests executed RED: 2 expected failures (unnecessary model-menu click despite selected high; 6000ms instead of 18000ms) and 1 negative-case PASS; no network.
- [x] CB16-03 Source fix: verify `data-selected-reasoning-effort` and skip dropdown when equal; wait 18s for slider if different; 7 focused tests GREEN.
- [x] CB16-04 Full pytest 218 PASS and Ruff E4/E7/E9/F PASS; verified wheel and source archive SHA256, source archive includes installer and reasoning-picker fix.
- [x] CB16-05 Fresh JKMac service backup 45MB, 7 SHA256 checks PASS; online SQLite integrity PASS, release tarball readable; rollback to beta.12 preserved.
- [x] CB16-06 Public prerelease `v0.2.0-beta.13` at https://github.com/johnnykor82/chatbridge/releases/tag/v0.2.0-beta.13 with source/wheel/SHA256 assets. Exact source commit `f1d50c5bc20fb7ddca8a564a4355f4b4a7b4d79e`; public main pushed. Temporary GitHub TLS timeout recovered.
- [x] CB16-07 JKMac `net.chatbridge.gateway` installed beta.13 `/Users/jk/MacControlMCP/deps/chatbridge/releases/0.2.0b13-21f327a77ef6` through official upgrade using SHA256-verified source. `/ready` 200, health OK, SQLite integrity OK; saved Chrome profile, original Watcher tasks and recovery backup preserved.
- [x] CB16-08 NEW session `bbfffa4b-f0d2-481b-8b1a-77991413da53`, request `d1461c37-5b9f-492c-bd52-075f8957e9ca` completed with `CB16_EXECUTOR_READY`, actual conversation `6aeb8422-faec-83ed-bf53-1ea094c4c26d`. Watcher real PRODUCTION DB independently resolved new high-entropy marker via ChatBridge, `cached=false`, evidence=true, matches=1 and exact URL, elapsed 11.0s; source `chatbridge_verified_user_message`. Previous failed request was NOT retried.
- [x] CB16-09 Converge: all 6 requirements met, 218 tests PASS, real first send and unique production marker lookup PASS, old Watcher tasks preserved 1/1/0, live SQLite healthy, 7-file predeploy backup SHA256 PASS. Final documentation committed.
