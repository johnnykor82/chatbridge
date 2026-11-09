# CB33 SPEC v1.0 — user-approved 2026-11-09
S1: Owner ChatBridge accepts up to existing 10 concurrent *different* sessions; no request may share a Chrome Page with another.
S2: Each visible-background Chrome tab is created via CDP Target.createTarget(background:true,focus:false), deterministically correlated with its returned targetId, with no cross-request context.expect_page capture.
S3: Serialize only tab creation and identity resolution if required. Navigation, submission, streaming and completion of distinct sessions MUST remain concurrent. Same session remains serialized.
S4: On failure to establish exact page ownership, fail closed before submitting a prompt, close only target created by this operation, never close/reuse another request's tab.
S5: On cancellation or provider 429, release per-request resources and use existing global cooldown. Never retry an uncertain POST; idempotent request receipts remain intact.
S6: Structured nonsecret diagnostics: per request/session targetId, allocation, navigation start/finish, completion, cleanup; keep browser/cookies/auth out of logs.
S7: Test RED->GREEN deterministic 2/3/5/10 tab ownership races, cancellation, mismatch/timeout, session isolation, first-ready independent completion, no global 10-slot throughput serialization, 429. Run relevant full offline tests.
S8: Only after validated snapshot and Converge, stage owner 59496 update without changing unrelated 8788/ChatBridge profiles, medical, GitHub or Mac Control; 2-worker longer live Watcher test where possible, respect 429.
S9: Documentation after each stage, final code-to-contract Converge and Git commit. Never claim 10 real simultaneous model requests on mock evidence alone.

CB33 P07R addition approved 2026-11-09: ChatBridge MUST NOT reject a nonempty plain-text message based on any arbitrary character count below actual selected ChatGPT platform/model context. Full 130k/1M UTF-8 examples remain byte-equivalent strings; the provider or transport reports its own real limits. No chunking, summarization or silent clipping.
