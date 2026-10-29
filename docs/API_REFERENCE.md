# HTTP API reference

ChatBridge exposes a local, unofficial gateway to an authenticated ChatGPT
web session. This reference covers the implemented experimental API,
not planned functionality.

Set the base URL to the port configured at installation (default `8787`).

```sh
export CHATBRIDGE_URL=http://127.0.0.1:8787
```

All endpoints accept and return JSON unless explicitly specified as SSE.
Set `Content-Type: application/json` on JSON requests. If
`CHATBRIDGE_API_KEY` is configured, send
`Authorization: Bearer <your-API-key>` on every request. Do not publish
your authenticated gateway over a public network.

Browser requests with an `Origin` header must match the gateway's exact
scheme and Host (including port); foreign origins, `null`, and duplicate Origin
headers return HTTP 403 before any operation. Requests without Origin, such as
CLI and server clients, continue to work without a key by default. This is a
browser boundary, not authentication for other local processes. Reverse proxies
must preserve the intended scheme/Host; no cross-origin browser UI is enabled.

## Endpoint index

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | HTTP process health and session counters |
| `GET` | `/metrics` | Active slots, peak slots, queue and event counters |
| `GET` | `/v1/system/status` | Chrome ownership, availability and supported thinking levels |
| `GET` | `/ready` | HTTP 200 only when Chrome and ChatGPT editor are usable |
| `POST` | `/v1/sessions` | Create a session or attach an existing ChatGPT conversation |
| `GET` | `/v1/sessions` | List stored sessions |
| `GET` | `/v1/sessions/{session_id}` | Read a session |
| `POST` | `/v1/sessions/{session_id}/messages` | Submit a new user message |
| `GET` | `/v1/sessions/{session_id}/messages` | Retrieve stored message history |
| `POST` | `/v1/sessions/{session_id}/sync` | Import the current ChatGPT history branch |
| `GET` | `/v1/sessions/{session_id}/turn-state` | Read verified final/ongoing ChatGPT turn |
| `GET` | `/v1/requests/{request_id}` | Read current state and aggregated answer |
| `GET` | `/v1/requests/{request_id}/events` | Resumable SSE request stream |
| `POST` | `/v1/requests/{request_id}/reconcile` | Reconcile an `unknown` request |
| `GET` | `/v1/media/{media_id}` | Download a stored original image or arbitrary file |
| `GET` | `/v1/models` | Compatibility placeholder (`auto`) |
| `POST` | `/v1/chat/completions` | Partial OpenAI Chat Completions interface |

There is currently **no** API endpoint for deleting sessions, cancelling
active tasks, selecting a different base model, or obtaining a complete
OpenAPI document. Images and arbitrary files can be uploaded in JSON or
multipart messages; see [attachments](ATTACHMENTS.md).

## 1. Health and metrics

```sh
curl -fsS "$CHATBRIDGE_URL/health"
curl -fsS "$CHATBRIDGE_URL/metrics"
```

`/health` reports `status: "ok"` when the HTTP process is responding.
**It does not verify that Chrome is running, signed in or usable.**
Use `/ready` for authentication/editor readiness and `/v1/system/status`
for browser state and thinking-effort defaults.
`/metrics` exposes `active_slots`, `max_active_slots`, `capacity`,
`queued`, `sessions` and `events`.

## 2. Create or attach a conversation

Create a new local session:

```sh
curl -sS "$CHATBRIDGE_URL/v1/sessions" \
  -H "Content-Type: application/json" \
  -d '{"title":"project-discussion"}'
```

Example response (IDs and timestamps vary):

```json
{
  "session_id": "00000000-0000-4000-8000-000000000000",
  "conversation_id": null,
  "title": "project-discussion",
  "url": null,
  "created_at": 0,
  "updated_at": 0
}
```

`conversation_id` is initially `null`. It becomes the real ChatGPT
conversation UUID once the first message is successfully sent.

Attach an existing ChatGPT conversation:

```sh
curl -sS "$CHATBRIDGE_URL/v1/sessions" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://chatgpt.com/c/00000000-0000-4000-8000-000000000000"}'
```

Instead of `url`, `conversation_id` can contain the UUID. Re-attaching
the same UUID returns the corresponding existing local session.

List and retrieve sessions:

```sh
curl -sS "$CHATBRIDGE_URL/v1/sessions?limit=50&offset=0"
curl -sS "$CHATBRIDGE_URL/v1/sessions/$SESSION_ID"
```

`limit` for listing is clamped to `1..250`, default `50`; `offset`
is zero-based and defaults to `0`.

## 3. Submit a message

```sh
curl -sS "$CHATBRIDGE_URL/v1/sessions/$SESSION_ID/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Summarize the last discussion in this conversation.",
    "client_request_id": "example-step-0001"
  }'
```

The `content` field must be a non-empty string with no more than
100,000 characters. `client_request_id` is optional but recommended for
reliable clients. It is an idempotency key within the session: resubmitting
the **same content with the same key** returns the existing request rather
than dispatching a duplicate. Reusing the same key with **different content**
returns HTTP `409`.

### Thinking effort: Low / Medium / High

ChatBridge uses **`high` by default on every new request**. Override it
per turn with `reasoning_effort`:

```json
{
  "content": "Analyze this design rigorously.",
  "client_request_id": "design-review-001",
  "reasoning_effort": "high"
}
```

Allowed levels are `low`, `medium` and `high`. The native web UI
slider uses the respective positions 0, 1 and 2. Before submitting the
message, the gateway sets and reads back the selected level. If the model
picker or slider is unavailable, it fails rather than silently sending a
message under a different setting. The web account may restrict available
settings, and `high` is a larger reasoning budget, not a guarantee of
correctness or a way to change the base model.

The effective value is persisted on each request and returned in the initial
response and in `GET /v1/requests/{request_id}`. Reusing a
`client_request_id` with different text, attachments **or thinking effort**
returns HTTP `409`, preventing accidental reuse of a different computation.
Older requests that predate explicit persistence and contain a null setting
are treated as the previous `high` default.

For a different service-wide default, set
`CHATBRIDGE_REASONING_EFFORT=medium` or `low`; a clean installation defaults
to `high`. `GET /v1/system/status` advertises `default_reasoning_effort` and
`supported_reasoning_efforts`. The partial `/v1/chat/completions` adapter
also accepts a top-level `reasoning_effort` field.

The endpoint responds promptly with HTTP `202` for a new queued request.
The body contains `session_id`, `conversation_id` (possibly `null`),
`request_id`, `status`, `status_url` and `events_url`.
Replaying an existing idempotency key returns HTTP `200`.

Requests in the same conversation are serialized. The configured
`CHATBRIDGE_MAX_ACTIVE` default is `10`: a further request is queued
until a worker slot becomes free. Each active task normally uses an
independent Chrome tab.

## 4. Poll for completion

```sh
curl -sS "$CHATBRIDGE_URL/v1/requests/$REQUEST_ID"
```

This endpoint returns the stored request record, including
`status`, accumulated `answer`, `last_error`,
`user_message_id`, `assistant_message_id`, `session_id`,
`conversation_id` and timestamps.

Main states:

| State | Meaning |
|---|---|
| `queued` | Waiting for another turn or a free slot |
| `submitting` | Browser submission in progress |
| `thinking` | Assistant has started the turn |
| `streaming` | Assistant text has been observed |
| `tool_running` | Tool-related work is in progress |
| `awaiting_approval` | MCP approval handling in progress |
| `completed` | Final turn was verified |
| `failed` | A confirmed failure |
| `interrupted` | Request explicitly interrupted |
| `unknown` | Outcome cannot yet be confirmed; avoid blind resubmission |

Do not treat `streaming` or an SSE stream completion marker as a final
answer. Only `completed` confirms a verified final assistant turn.

## 5. Subscribe to resumable SSE

```sh
curl -N -H "Accept: text/event-stream" \
  "$CHATBRIDGE_URL/v1/requests/$REQUEST_ID/events"
```

Each event includes a strictly increasing event ID and JSON body:

```text
id: 7
event: text_delta
data: {"request_id":"...","session_id":"...","conversation_id":"...","type":"text_delta","text":"Hello"}
```

Observed event kinds include `status`, `text_delta`, `text_replace`,
`approval_applied`, `tool_started`, `tool_finished`, `completed`
and `error`. The source may emit multiple thinking/tool/text phases.

**Important:** `text_replace` means replace any provisional accumulated
answer with the provided `text`; do not blindly concatenate all deltas.

Resume from the last processed event ID:

```sh
curl -N -H "Last-Event-ID: 7" \
  "$CHATBRIDGE_URL/v1/requests/$REQUEST_ID/events"
```

You may alternatively append `?after=7`. The server persists events in
SQLite. Client disconnection does not cancel an executing ChatGPT turn.

## 6. Retrieve and synchronize history

Read locally saved messages:

```sh
curl -sS "$CHATBRIDGE_URL/v1/sessions/$SESSION_ID/messages?limit=100&after=0"
```

The response has `data` and `next_cursor`. Set `after` to the
returned cursor for another page. Use `limit=1..500`.

Import the current conversation branch from ChatGPT into the local store:

```sh
curl -sS -X POST "$CHATBRIDGE_URL/v1/sessions/$SESSION_ID/sync"
```

This is a snapshot of the **currently selected backend branch**, not
necessarily all alternate regenerated/branched assistant answers. It may
include tool messages when available. The current history pagination and
mixed imported timestamps warrant additional testing before claiming
complete chronological integrity.

## 7. Recover an uncertain request

After a process interruption, in-flight requests may be marked
`unknown`. If the gateway has a verified user message ID, it may locate
the final assistant message in ChatGPT's history without sending anything
again.

```sh
curl -sS -X POST \
  "$CHATBRIDGE_URL/v1/requests/$REQUEST_ID/reconcile"
```

This returns `request_id`, `reconciled` (boolean), and `status`.
A response with `reconciled: false` is not proof that the message was
never sent. Inspect the ChatGPT conversation before submitting again.

## 8. OpenAI Chat Completions compatibility

Non-streaming example:

```sh
curl -sS "$CHATBRIDGE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello"}]}'
```

Use `"session_id":"..."` to reuse a saved ChatBridge session.
Alternatively supply `"conversation_id":"..."` to import an existing
ChatGPT conversation.

The gateway currently sends **only the last user message** from the
`messages` array. Earlier system/developer/user/assistant items in that
array are **not replayed as an OpenAI-style prompt history**. Conversation
context comes from the persisted ChatGPT conversation associated with the
session. For this reason, compatibility with arbitrary OpenAI API clients
is **partial**.

Set `"stream":true` to obtain a Chat Completions-shaped SSE response,
but it currently buffers the whole reconciled assistant answer and emits
it as a single content chunk. To follow live work and MCP status, prefer
the native `/v1/requests/{id}/events` API.

`GET /v1/models` returns a placeholder `auto`, not a dynamically
queried complete model list. There is no guaranteed model-selection API.

## 9. Errors

| HTTP code | Example meaning |
|---|---|
| `200` | Successful read or replay of an idempotent request |
| `201` | Local session created |
| `202` | New user message accepted for async processing |
| `400` | Invalid input / unsupported text content |
| `401` | Invalid or missing configured API token |
| `404` | Unknown session or request |
| `409` | Idempotency key reused with different message |
| `502` | History synchronization or a compatibility completion failed |
| `504` | Compatibility completion exceeded its waiting deadline |

`GET /health` being successful does not imply a working browser
connection, valid ChatGPT authorization or permission to use MCP tools.

## 10. Command-line and macOS operations

Development invocation:

```sh
uv run chatbridge
uv run chatbridge --port 8787
uv run chatbridge --mock --port 8790 --db /tmp/chatbridge-mock.db
uv run chatbridge --help
```

The implemented CLI options are `--mock`, `--host`, `--port`
and `--db`. Remaining settings are environment variables (see README).
`--mock` uses a fake Echo transport and **does not access ChatGPT**.

On macOS after installing the LaunchAgent:

```sh
launchctl print gui/$(id -u)/net.chatbridge.gateway
launchctl kickstart -k gui/$(id -u)/net.chatbridge.gateway
tail -f "$HOME/.chatbridge/service.stderr.log"
```

See [macOS LaunchAgent](MACOS_LAUNCHAGENT.md) for installation, stop/start,
restart supervision, file locations and service limitations.

## 11. MCP approval and security notes

The gateway can interact with permission prompts shown by ChatGPT for its
connected MCP tools and maintains an approval audit in its local database.
Connected tools may have consequential capabilities. Users must understand
the implications of running an autonomous gateway with their signed-in
account; see [SECURITY.md](../SECURITY.md).

The gateway cannot override provider-side authorization or other hard
rejections. A clicked UI element alone is not proof that an MCP operation
executed; verify tool events and the returned result.

Treat the HTTP API like an account-level credential. It can issue prompts to
a signed-in browser. The default loopback listener has no API token unless
one is configured. Use a strong API token and do not bind externally without
access controls.

## Images (version 0.2)

`POST /v1/sessions/{id}/messages` now accepts image content as JSON
`images[]` (base64/data URL) or multipart image uploads. Responses expose
`images` and `image_refs`; `GET /v1/media/{media_id}` returns image bytes.
`/v1/chat/completions` also accepts mixed `text` + `image_url` parts
with inline data URLs; this remains partial OpenAI compatibility.
See [Images](IMAGES.md) for complete request examples and limitations.

## All file formats (experimental 0.2)

Native API JSON supports `attachments[]` containing `filename`, `mime_type`,
and `data_base64`, or multipart file uploads. Mixed images, audio, PDF, Word,
Excel, text, scripts and unknown extensions are stored as binary data. File
support in ChatGPT varies by account. The response includes `attachments`,
`images`, `artifact_refs`, `attachments_complete`, and `attachment_errors`.
Download with `GET /v1/media/{media_id}`. See [full guide](ATTACHMENTS.md).

## Read provider-confirmed latest turn (Watcher integration)

GET /v1/sessions/{session_id}/turn-state returns last message ID, role, provider
status, completed/generating booleans and final text only when the last message
is an assistant response with end_turn=true and status=finished_successfully.
The endpoint reads ChatGPT via the gateway's owned browser and does not alter
the conversation. It enables an HTTP-only Watcher to determine executor task
completion without DOM/CDP access. A tool message or an in-progress assistant
message cannot be classified as completed.
