# Arbitrary file attachments in ChatBridge

> Status: experimental 0.2.0-alpha.1. Multimodal support is deployed on the
> developer Mac LaunchAgent, but standalone browser installation is not certified.

## File handling model

ChatBridge transports opaque file bytes to and from an authenticated ChatGPT
web session. The gateway **does not execute** downloaded scripts or programs.
Its own acceptance rules concern type detection, storage size and transport;
whether ChatGPT can *understand or process* a particular format depends on
the ChatGPT account, model, plan and provider features.

Input accepts photographs, audio, PDF, Office files, text/code, archives,
and arbitrary filename extensions. It does not promise ChatGPT itself will
accept every extension, and it does not download client-provided remote URLs.

### Upload a file using HTTP multipart

```sh
export CHATBRIDGE_URL=http://127.0.0.1:8787
curl -sS -X POST "$CHATBRIDGE_URL/v1/sessions" \
  -H 'Content-Type: application/json' -d '{"title":"file-analysis"}'

# Replace SESSION_ID with session_id from the create response.
curl -sS -X POST "$CHATBRIDGE_URL/v1/sessions/SESSION_ID/messages" \
  -F 'content=Analyze the audio and summarize the PDF.' \
  -F 'client_request_id=mixed-attachment-task-001' \
  -F 'attachments=@meeting.wav;type=audio/wav' \
  -F 'attachments=@report.pdf;type=application/pdf'
```

Use any multipart field name for attached file parts; the gateway takes
`part.filename` as an attached file. For JSON, provide an array of
`attachments` with `filename`, optional `mime_type`, and `data_base64`
(or a `data_url`). Images can additionally be passed through `images[]`.

```json
{
  "content": "Summarize the attached documents.",
  "attachments": [
    {
      "filename": "report.docx",
      "data_base64": "BASE64_ENCODED_FILE_BYTES"
    }
  ],
  "client_request_id": "file-analysis-task-001"
}
```

The OpenAI-compatible text/parts facade also recognizes `type: "file"`
with an inline `file` object, but it is **not** fully compatible with every
OpenAI stateless message-history or remote file-upload API.

A request may consist entirely of one or more files without text.
An idempotency key identifies the ordered list of file hashes as well as
the accompanying text. A different file under the same key returns HTTP 409.

### Size limits of the current experimental gateway

- Up to 12 attached files per request and 45 MiB of combined raw bytes.
- Up to 8 images passed through `images[]`, each no larger than 10 MiB.
- An individual generic attachment is limited to 25 MiB.
- The JSON body with base64 expansion is subject to the HTTP body limit.
- The account/browser may impose a **different** or tighter limit.

These are technical limits of this implementation, not a guarantee of
ChatGPT support. They will require a streaming-blob redesign to allow
hundreds of megabytes without holding entire files in RAM/SQLite BLOBs.

## Retrieve files generated or attached by ChatGPT

```sh
curl -sS "$CHATBRIDGE_URL/v1/requests/REQUEST_ID"
```

The `attachments` array enumerates stored media for that request with
`media_id`, role, original filename, MIME type, SHA-256 and length.
The `images` array includes only image MIME types for backwards
compatibility. `artifact_refs` exposes provider references found in the
tool and assistant messages.

```sh
curl -fsS "$CHATBRIDGE_URL/v1/media/MEDIA_ID" -o report.pdf
```

Media responses include the original binary bytes. Scripts and documents
are served with `Content-Disposition: attachment` so the web browser
downloads rather than executes/displays a potentially active file. Images
can be served inline. Retrieval requires the gateway's API credentials
if an access token is configured.

SSE events may include `artifact_refs`, `image`, `artifact` and
`file_unavailable`. An `artifact` event supplies the `media_id`,
`mime_type`, `filename` and `url` for download.

The `GET /v1/requests/{id}` response includes
`attachments_complete` and `attachment_errors`. A request may finish
normally even if some provider files cannot be downloaded; clients must
check this flag rather than equating `completed` with complete media.

## How provider downloads work

1. Asset references from tool/assistant messages are correlated to the
   original user turn; each distinct reference is resolved and downloaded.
2. Some files have **no file asset pointer**. ChatGPT instead renders
   `sandbox:/mnt/data/... ` as clickable resource cards inside its
   assistant message. ChatBridge downloads *every card*, retaining
   filename, MIME and bytes. If expected cards are absent, it reports a
   `file_unavailable` event.
3. Duplicate retrieval of the same asset through an asset pointer and a
   rendered card is suppressed. Separate files with different references
   or different resource cards are retained, even when their bytes match.
4. Credentials and temporary signed URLs remain inside the authenticated
   browser; the HTTP consumer receives **local** media IDs and bytes only.

## Confirmed live scenarios

- **Two generated images in one reply:** two PNGs downloaded independently.
- **Four files in one reply:** TXT, CSV, Python source, PDF; four binary files
  saved and served intact, no duplicates.
- **Audio input:** WAV attached via HTTP; ChatGPT returned a transcription/
  interpretation with an intentionally planted spoken control word.
- **Image input:** PNG attached via HTTP; ChatGPT identified the shapes and
  their colours.
- **Real document download cards:** CSV and Python source downloaded and
  verified independently.
- Word/Excel/ZIP require an additional live test; programmatic unit tests
  cover Office-format MIME and arbitrary extensions.

## Operational caveats

- File storage currently uses SQLite BLOBs. Back up the database, avoid
  placing it on a world-readable volume, and monitor disk growth.
- The user-authorized browser may log out, reject an extension, run out of
  account resources, or change its DOM/backend contract at any time.
- External HTTP endpoints should stay bound to loopback unless protected.
- The API does **not** fetch arbitrary URLs to avoid SSRF and account leakage.
- Existing-chat history synchronization of all past files, not just files
  produced in new ChatBridge turns, requires a separate migration/test.
- This milestone is independent of the later plan to give ChatBridge its
  own Chrome profile and migrate Watcher to the HTTP API.
