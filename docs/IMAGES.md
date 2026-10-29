# Images over ChatBridge HTTP API

Status: experimental 0.2.0-alpha.1. Integrated into the development Mac
LaunchAgent; clean-install and isolated-Chrome certification remain pending.

## Input: images supplied by API callers

Supported formats: PNG, JPEG, WebP, GIF. Each image is at most **10 MiB**;
a request may include at most **8 images** and at most **24 MiB** of combined
raw image bytes. Uploaded MIME types are verified against the file signature.
Invalid images receive HTTP 400. Remote HTTP/HTTPS image URLs are not
downloaded by ChatBridge.

All endpoints below use the configured host/port:

```sh
export CHATBRIDGE_URL=http://127.0.0.1:8787
```

Create a conversation first:

```sh
curl -sS -X POST "$CHATBRIDGE_URL/v1/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"title":"photo-analysis"}'
```

Copy the returned `session_id` to `SESSION_ID`.

### Multipart (recommended for large images)

```sh
curl -sS -X POST \
  "$CHATBRIDGE_URL/v1/sessions/SESSION_ID/messages" \
  -F 'content=What is shown in this photograph?' \
  -F 'client_request_id=photo-task-0001' \
  -F 'images=@example.png;type=image/png'
```

Repeat the `-F 'images=@...' ` argument to attach more than one image.
`content` is optional if at least one image is attached.

### JSON with base64

```json
{
  "content": "Describe this photograph.",
  "client_request_id": "photo-task-0002",
  "images": [
    {
      "filename": "example.png",
      "mime_type": "image/png",
      "data_base64": "BASE64_ENCODED_PNG"
    }
  ]
}
```

Instead of `data_base64`, use `data_url` containing
`data:image/png;base64,...`. Filename and MIME may be omitted for
`data_url`; the server inspects file signatures. Each message's
idempotency hash includes the text and ordered image content. A changed
image with the same `client_request_id` results in HTTP 409.

### OpenAI Chat Completions-compatible input

```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What's in this picture?"},
        {
          "type": "image_url",
          "image_url": {"url": "data:image/png;base64,BASE64_ENCODED_PNG"}
        }
      ]
    }
  ]
}
```

The compatibility route is still not a full OpenAI API implementation:
only the latest user message is dispatched and ordinary OpenAI system
messages are not replayed.

### Transport behavior

ChatBridge attaches the bytes to the authenticated Chrome composer using
the browser's ordinary file input. It waits for processing of the image
preview before submitting the message. Upload filenames are randomized to
avoid Chrome/ChatGPT's automatic duplicate-filename suffixes. If preparation
fails, the request fails before submitting rather than blindly retrying.
The complete request is correlated to its original ChatGPT user message.

## Output: images returned by ChatGPT

The normal request lifecycle and SSE event stream still apply. The gateway
also returns:

- `images`: list of actually stored binary assets, each with a unique
  `media_id`, filename, MIME type, SHA256, size and role.
- `image_refs`: image references from ChatGPT history, including tool-generated
  images where the final assistant text message has no attachment.

A stored image can be downloaded from:

```sh
curl -fsS "$CHATBRIDGE_URL/v1/media/MEDIA_ID" -o chatgpt-image.png
```

Use `GET /v1/requests/REQUEST_ID` to retrieve the list of media assets,
or listen for `image` / `image_refs` SSE events. The `image` event
provides the media ID and `url` path. The media endpoint returns actual
original image bytes, not a base64 string.

### How generated images are retrieved

Generated images may be represented by **tool messages** containing
`image_asset_pointer` entries (e.g. `sediment://file_...`), while the
final assistant text message is empty. ChatBridge examines the tool and
assistant nodes belonging to the same turn. It resolves image assets using
the authenticated ChatGPT browser and fetches the binary bytes. Temporary
signed URLs and authorization headers stay inside that browser and are not
returned to API clients.

A reference in `image_refs` is **not** proof that the image is downloadable.
Check that it also appears in `images`. Unsupported/unreachable references
are kept for diagnosis and must not be represented as successful downloads.

## Storage and backup

Image bytes currently live as SQLite BLOBs in the gateway's persistent state
database, and media metadata is bound to the request and session. Protect and
back up the SQLite database accordingly. Storing large binary assets in SQLite
is convenient for this first implementation, but external blob storage,
configurable retention and garbage collection remain tasks for later phases.

`GET /health` only confirms that the HTTP process is alive; it does not
confirm the Chrome browser, ChatGPT authentication or image generation quota.

## Validation

- Tests for MIME validation, base64, empty/image-only messages, multipart,
  idempotency, SSE, response media and binary retrieval.
- Live upload: a synthetic test PNG containing a red triangle and blue
  rectangle was recognized correctly by ChatGPT.
- A generated ChatGPT PNG was retrieved through the authenticated browser
  and served byte-identically through the HTTP media endpoint.
- Full end-to-end image generation through the asynchronous HTTP scheduler
  passed, including one generated image and two separate images in one turn.

Other media (audio, video, documents) are not included in this milestone.
