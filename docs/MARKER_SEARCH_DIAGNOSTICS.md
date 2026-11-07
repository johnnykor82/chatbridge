# ChatBridge marker search diagnostics (beta.21)

ChatBridge resolves a previously published WC+16hex or legacy marker only within
the existing authorized owner-managed Chrome context. It never creates a new
conversation or sends a message while searching.

Resolution first checks bounded recent sidebar and provider-history candidates.
If none can be verified, it opens ChatGPT's standard Search Chats dialog, trying
the visible Search buttons normally in sequence (no forced clicks). It first waits boundedly for Search/sidebar controls to hydrate after page navigation,
then **fills the entire marker before looking at search results**. The initial unfiltered
"Recent chats" list is not evidence of a match. Candidate conversations must
still contain an exact user or assistant message marker and match the provider's
real conversation ID; Watcher requires an assistant-message match.

To troubleshoot a missing sidebar/search dialog, owner-only service logs include
"Marker lookup DOM stage=..." with allowlisted metadata **only**:
- Document readiness (loading, interactive, complete, unavailable)
- Number of ChatGPT conversation anchors present in the resolver-owned page
- Number of Search buttons and how many are visible
- Number of visible dialogs, their visible search inputs, and dialog chat anchors

No marker, URL, chat title, history content, HTML, cookies, bearer token,
provider credentials or conversation ID is included. DOM telemetry itself is
bounded and fault-tolerant; it does not bypass authorization or provider rate limits.

If the full marker search returns no eligible result, a legacy long marker may
retry its 32hex segment as an index term; full message verification remains
mandatory. A compact WC+16 marker does not repeat the same query.
The independent real-provider lookup is a separate acceptance gate; offline
tests alone do not prove the ChatGPT web interface is searchable.
