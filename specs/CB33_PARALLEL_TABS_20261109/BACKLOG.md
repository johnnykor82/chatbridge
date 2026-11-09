# CB33 BACKLOG — 2026-11-09
- Optional broader 5–10 genuine ChatGPT live requests only if justified and provider limits allow; offline 10-tab tests sufficient for initial release gate.

- P06R new: redesign Supervisor evidence representation to include verified tail of long answer (or reliable deterministic metadata), retaining exact source turn provenance and fail-closed. RED test >12000 chars with end sentinel and GREEN, source Converge, versioned commit+owner deployment, new review test; never change existing A goal without separate authorization.
- P06R new: replace over-conservative Watcher busy() global health.active>0 delay with per-orchestrator session-active verification and capacity/rate-limit backpressure, proving B notification delivery while A streaming and preventing HTTP429. Separate architecture approval and live low-load test required.
- P06R test monitor has overlap=false despite statuses streaming/streaming; raw timestamped sample proves overlap. Expand future observer state classifier to include streaming/thinking/submitting without changing immutable prior log.
