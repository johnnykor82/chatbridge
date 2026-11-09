# CB33 TASKS v1.0
- [x] P01 Public Spec Kit Agentic SDD, Evolving Specs, Bug Fix read. Live snapshot + profile snapshot SHA256 checked.
- [x] P02 Analyze consistency PASS; 2/3/5/10 shared-tab RED races plus cancellation and target mismatch reproducible.
- [x] P03 Narrow lock + exact targetId assertion + CDP-owned resource cleanup + metadata-only lifecycle logs; 4 GREEN test methods and isolated Chrome 2/10 actual CDP verified.
- [x] P04 Offline 2/3/5/10 concurrent allocations, cancellation, mismatch, 429/no resend; full 271/4 suite, real Chrome 2/10, lint and Converge. Git commit next.
- [x] P05 Owner-only immutable release deployment 0.2.0b23-7ffed7851c50, source SHA256, previous release/rollback and /ready health verified.
- [ ] P06 BLOCKED: current orchestrator chat already owned by paused BF33 project; two new jobs rejected before sending. Owner permission required to reuse/resume that existing project or a different approved routing solution; no retries.

- [x] P06R-01 user amendment 2026-11-09: plan revisited and current Spec Kit consulted.
- [x] P06R-02 verified online SQLite Watcher backup + old rows SHA256, before changes.
- [ ] P06R-03 BLOCKED: existing completed ACKed old browser tasks still watching and will be reprocessed on resume. Requires new owner-approved Watcher code safeguard, RED/GREEN regression, Converge, scoped Git commit/deploy before unpause.
- [ ] P06R-04 then exactly two new live workers with independent in-flight overlap/confirmed immutable reports/ACK; stop on 429.
- [ ] P06R-05 re-pause original project, compare old immutable rows to backup, final Converge/Git commit.
