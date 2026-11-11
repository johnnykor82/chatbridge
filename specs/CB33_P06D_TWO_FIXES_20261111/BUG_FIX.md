# CB33 P06D Bug Fix — 2026-11-11

F04 ChatBridge: `src/chatbridge/browser_transport.py` now detects one transient Playwright slider visibility timeout, checks cooldown and dismisses/reopens the picker once strictly before Send, then rechecks actual `aria-valuenow`; persistent missing menu still raises before submission. Updated legacy assertion and 3 new regression tests. Scoped `test_reasoning_picker_runtime.py` + `test_cb33_p06d_reasoning_recovery.py`: **6 passed**. No Send retry, mode downgrade or timeout bypass.

F04 Watcher: isolated worktree based on exact installed commit 749a748. `watcher_http_adapter.py` adds GET-only bounded local session lookup by exact conversation identity and project, fails closed on duplicates, unexpected modes or mismatch. `ensure_session` reuses existing standard conversation instead of HTTP409 persistent re-import; previous delivery links accept verified standard/continued/persistent with route guard. Original persistent import remains only when no local match. New in-memory regression tests: **4 passed**. No live service deployed, no original BF33/UNKNOWN mutation.

Full suite, static Converge, versioned deployment and E2E remain OPEN.
