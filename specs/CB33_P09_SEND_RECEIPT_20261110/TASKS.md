# CB33 P09 Tasks — 2026-11-10
- [x] P01 official methodology, read-only Assess, verified backup, isolated branch and Analyze
- [x] P02 four original RED failures plus independent POST source ID RED; foreign prompt rejection
- [ ] P03 BrowserTransport+Engine implemented, six P09 focused and 30 lifecycle GREEN; full safety review/additional negative tests pending
- [x] P04 full 317 tests/4 subtests PASS, critical Ruff/compile/whitespace PASS, source Converge partial, Git source commit e179b55 complete
- [x] P05 owner-only immutable 0.2.0b23-5cc6919bcdaa release, verified backup+rollback, authenticated ready, 8788 and old BF33 intact
- [ ] P06 new two-worker live test, delivery/ACK and preserve original BF33
- [ ] P07 final Converge/docs/Git

- [x] P06A-1 current owner backup verified, exact pre-Send listener failure assessed and SDD updated
- [x] P06A-2 RED unrelated nonJSON POST reproduces Playwright Error; 2 control tests PASS
- [x] P06A-3 source filter; 14 focused and full 320 tests/4 subtests PASS, Ruff/compile/Converge PASS; Git commit next
- [x] P06A-4 owner-only immutable 0.2.0b23-6f387abafb22 release verified auth/ready/no429, backup rollback, 8788 untouched, original BF33 old hashes 9/9 match
- [ ] P06A-5 distinct orchestrator bootstrap after definitive pre-Send failure, exactly two fresh workers/ACK and final Converge

- [x] P06B-1 2 RED native button and exception cases, 2 GREEN Enter fallback controls
- [x] P06B-2 one-gesture fix, native Send click/Enter fallback/exception tests GREEN; combined18 focused tests pass
- [x] P06B-3 full 324 tests + 4 subtests PASS; Ruff/compile/diff PASS; source Converge/SDD docs verified; Git commit e9c78f7. Owner deploy only after in-flight request terminal
- [x] P06B-4 scoped owner-only immutable release 0.2.0b23-6fa955903057; fresh SHA/SQLite-verified backup, auth/health/no429 and 8788 PASS; original UNKNOWN not resent
- [ ] P06/P07 isolated two-worker live E2E and final Converge PREVIOUSLY BLOCKED by terminal UNKNOWN; owner explicitly approved a NEW isolated conversation on 2026-11-11. Existing UNKNOWN must not be retried.

## P06C owner-approved 2026-11-11 expansion
- [x] P06C-01 official Agentic SDD/Evolving Specs verified online; SPEC/ARCH/PLAN/TASKS/ANALYZE PASS; backup 4 SHA256 and 2 SQLite integrity ok; BF33 paused; preflight no429
- [x] P06C-02 one fresh isolated ChatBridge :59496 session; one HTTP202 bootstrap request terminal completed with user+assistant IDs, unique assistant marker WCcf405a1c82222f0e verified by Watcher, original unknown untouched
- [ ] P06C-03 PARTIAL: registered isolated project and queued exactly two new workers. A FAILED PRE-SEND reasoning slider timeout (Watcher uncertain; no resend); B completed with provider HTTP accepted. True overlap NOT PROVEN.
- [ ] P06C-04 BLOCKED: B immutable SHA-verified report and Supervisor review_ready; notification READY attempts0 (Watcher session_import TransportError due standard/persistent mode mismatch); no ACK/approval permitted, A pre-Send failure. No delivery-order/overlap acceptance.
- [ ] P07 Converge, verify BF33 original immutability, update SDD docs and Git commit, do not claim unverified acceptance
