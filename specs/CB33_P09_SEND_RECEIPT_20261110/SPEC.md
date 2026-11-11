# CB33 P09 SPEC v1.0 — owner approved 2026-11-10
R1: Never resend, ACK or alter original A uncertain request or any old BF33 task without unique exact authenticated provider proof. Original BF33 stays paused.
R2: Persist source-bound Send attempt BEFORE one UI click/Enter; restart and cancellation never trigger duplicate Send. Client-generated fetch message ID alone does NOT prove server receipt.
R3: Remove fixed 1.3s ID failure. Observe local DOM and network response after Send until source-confirmed receipt or existing configured turn timeout/explicit terminal error. No new prompt/response size caps or chunking.
R4: Watcher receipt-only completion needs both valid original user message ID AND successful HTTP provider response (or strict authenticated provider history proof). No response or HTTP429 must remain unknown.
R5: Persist metadata-only stages, not credentials/prompt bodies. Preserve GET-only durable R9 recovery backoff and provider429 stop.
R6: RED/GREEN late ID/HTTP200, absent ID, post-Send 429, cancelled/restarted, one Send, large complete answer and unchanged provider text; full suites/Converge/Git, reversible owner-only release.
R7: New two-worker test in isolated new project: simultaneous execution, out-of-order delivery/ACK, no unverified retries. Old BF33 unchanged. Pause on 429.

P06A 2026-11-10: Network observer must ignore unrelated HTTP POST requests before reading/parsing body. Unrelated non-JSON telemetry POST cannot crash Playwright event dispatch, interfere with reasoning picker, or change Send state. Only original ChatGPT full-prompt turn request can contribute evidence. Malformed original POST must fail closed without leaking body or retrying. Never impose arbitrary content limits.

P06B new-chat send path 2026-11-10: For fresh conversation, prefer an enabled native ChatGPT Send button exactly ONCE, just as for existing conversation, instead of unconditionally Enter. If no enabled button exists use one Enter fallback; never click then Enter on uncertain outcome. Original attempt journal must be persisted before either gesture and HTTP source evidence required before receipt. No changes to old BF33 or currently in-flight P09 bootstrap.
