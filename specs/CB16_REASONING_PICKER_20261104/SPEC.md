# CB-16 — ChatBridge first-message failure when reasoning slider appears late
**Specification v1.0 • 2026-11-04 • owner-approved scoped bugfix, no other feature changes.**

## Observed incident
On JKMac-2, a new ChatBridge persistent session request `bc57bdfd-a30f-442f-b5b9-ddca611341b5` failed before message submission: `TimeoutError: Locator.wait_for [data-reasoning-slider] 6000ms`; `conversation_id=null`. Live read-only inspection proved current ChatGPT model picker button has `data-selected-reasoning-effort=high`, with zero sliders BEFORE opening, and a visible `data-reasoning-slider` AFTER click. Thus unnecessary model-menu opening can cause a race on first sends. No prior failed request may be blindly resent.

## Requirements / acceptance
- CB16-R01 **Preflight:** if model picker explicitly advertises required `data-selected-reasoning-effort`, accept that existing level and skip opening dropdown; do not invent levels from text/model name.
- CB16-R02 **Selection:** when current level differs or is unavailable, open normal model picker, wait for real slider (up to 18 seconds), adjust only before send, verify slider's `aria-valuenow` equals requested level. If no valid evidence, fail closed before sending.
- CB16-R03 **Safety:** preserve exact `low/medium/high` semantics, no silent fallback/assumed success, no duplicate send or new privilege, old browser profile and sessions preserved. Keep background tab focus-free.
- CB16-R04 **Tests:** TDD RED/GREEN for already-correct high without slider, mismatch/slider transition, unavailable selector fails before send, full existing tests.
- CB16-R05 **Release/rollback:** publish new version after successful tests, create verified JKMac backup of ChatBridge release/plist/config/SQLite before update, install using Mac Control-managed dependency workflow without replacing legacy/other services. After update prove ChatBridge health and original data intact.
- CB16-R06 **Live acceptance:** new persistent session, one unique safe marker prompt via ChatBridge, capture actual response and `conversation_id`; Watcher beta.40 independently resolves marker through ChatBridge and returns verified exact URL. Mark outcome partial if provider search index returns 404/409.
