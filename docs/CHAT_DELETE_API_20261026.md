# ChatBridge cleanup API — 2026-10-26

Status: developed and mock-tested in isolated `feature/chat-delete-api` worktree.

## Scope and invariants

ChatBridge now separates **remote ChatGPT chat deletion**, **local SQLite
purge**, and **read-only classification**. For remote deletion the transport
uses the authenticated ChatGPT web origin, with the same browser that handles
existing chats. This uses ChatGPT's *private web interface*
(`PATCH /backend-api/conversation/<id>` with `is_visible:false`),
NOT the independent OpenAI Platform Conversations API. The web interface is
undocumented and may change. Do not claim success from HTTP 200 alone:
perform an authenticated read-after-write and accept only `is_visible:false`
or an absent conversation. On failure or uncertainty, quarantine the
operation; NEVER retry the destructive request automatically.

API deletion is **blocked** for project-scoped, rotated, persistent,
continued, temporary, medical or Watcher-titled sessions, and for any session
with queued, unknown or inflight requests. A title can be incomplete metadata:
all operations ALSO require an exact session ID, conversation ID, exact title,
and an idempotency key. The gateway binding rules apply; if an API key is configured it is required.
The default loopback API remains accessible without a key.

No change to the approval policy for MCP calls.

## Endpoints

- `GET /v1/sessions/{sid}/deletion-preview`: local-only, safe candidate
  classification; never requests ChatGPT.
- `POST /v1/cleanup/plan` with
  `{"title_prefix":"example-project","limit":100}`: bounded, dry-run-only local inventory.
  It deliberately does not delete any chats.
- `POST /v1/sessions/{sid}/deletion` with
  `{"confirm":"delete_remote","client_request_id":"stable-unique-operation-key",
     "expected_conversation_id":"<uuid>","expected_title":"<exact title>"}`.
  Results `remote_deleted` or `unknown`, each persisted with operation ID.
  Repeating the same request key never issues another PATCH.
- `GET /v1/cleanup/operations/{operation_id}`: inspect status and audit.
- `POST /v1/cleanup/operations/{operation_id}/reconcile`: read-only
  post-failure verification. It cannot retry remote deletion.
- `DELETE /v1/sessions/{sid}?operation_id={operation_id}` with header
  `X-Confirm-Purge: purge-local`: removes **local** SQLite session, messages,
  requests, events, media, refs and MCP audit rows in one transaction **only**
  after remote deletion was verified. Operation journal remains with state
  `purged`. Idempotent.
- For a `standard` session with **no physical conversation ID**, send
  `DELETE /v1/sessions/{sid}` with header
  `X-Confirm-Purge: purge-unlinked` and JSON
  `{"confirm":"purge_unlinked","client_request_id":"stable-key",
     "expected_title":"<exact title>"}`.
  Does not touch the ChatGPT provider.

## Bounded CLI

Dry-run:

```bash
python3 scripts/cleanup_chats.py --prefix example-project --limit 10
```

Execute after reviewing dry-run output:

```bash
python3 scripts/cleanup_chats.py --prefix example-project --limit 5 \
  --execute --confirm DELETE-example-project --campaign-id example-cleanup-v1 \
  --delay-seconds 25
```

The CLI calls only ChatBridge on localhost; never the provider directly.
One candidate at a time; before each candidate it checks local gateway
cooldown and active requests. It stops on a failed, pending or unknown
result. 1..20 candidates per invocation; no concurrency. No model prompts
are sent to perform cleanup. The per-session deletion operation is
idempotent across CLI interruption/restart.

## Out of scope / requires separate verifiable implementation

Project deletion is **not** equivalent to deleting an individual chat:
ChatGPT project deletion also removes project instructions and project-only
files, and can affect chats not in the local SQLite catalog. No destructive
project endpoint is enabled without verified provider membership and an
independently-tested UI path. Existing ChatBridge projects remain intact.

Automatic rollback cannot recover a ChatGPT chat after remote deletion.
Local SQLite backups can restore *local* metadata/content only. Always take a
SQLite online backup before real cleanup. This mechanism does not discover
all chats in the entire account: its inventory covers ChatBridge-owned
sessions and requires explicit classification.
