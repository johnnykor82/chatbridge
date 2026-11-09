# CB33 BUG TEST — 2026-11-09
VERIFIED offline / pre-live:
- Previous source RED: 4 test functions, 6 assertions failed: same Page returned to 2,3,5,10 concurrent owners; no cleanup on cancellation; no targetId assertion.
- New source GREEN: 7 tests (4 initial + 3 safety edges), every 2/3/5/10 slot assigned exact distinct CDP target, mismatch fails closed without touching foreign Page, cancel before and after create receipt closes exact target, event timeout closes exact target, post-allocation navigation tasks overlap.
- Isolated genuine local headless Chrome CDP (about:blank only, distinct temporary profile): concurrent allocation 2 and 10 exact target IDs validated using BrowserContext.new_cdp_session(Page); no owner Chrome or provider touched.
- Existing ChatBridge engine test test_ten_parallel_and_eleventh_queued passed with test-only pytest-asyncio; full suite 271 passed, 4 subtests in 14.39 s, exit 0; source and new tests ruff check pass; Python compile and git diff --check pass. No 429 or request replay introduced.
- Live ChatGPT Watcher 2-long-worker overlap and notifications/ACK remain UNTESTED until P06.
