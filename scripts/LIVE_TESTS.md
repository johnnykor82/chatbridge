# Opt-in live E2E tests

These are manual tests. **They create real ChatGPT conversations, may consume
generation quotas and may invoke ChatGPT tools.** CI runs only offline tests.

Install the gateway development environment first. Use an isolated, authorized
Chrome profile; do not depend on another service's CDP when validating
standalone installation.

```sh
cd chatbridge
uv sync --extra dev
export PYTHONPATH=src
export CHATBRIDGE_CDP=http://127.0.0.1:9437
uv run python scripts/office_files_http_live.py
```

| Script | Verifies |
|---|---|
| `generated_image_http_live.py` | One generated image, real bytes returned over HTTP |
| `multi_image_http_live.py` | Two distinct generated images in a single response |
| `files_http_live.py` | Two downloadable files, CSV and Python script |
| `office_files_http_live.py` | Four output files including a PDF |
| `word_xlsx_zip_live.py` | Word, Excel and requested archive; a missing ZIP must be reported |
| `image_http_live.py` | Vision with `/tmp/chatbridge_multimodal_probe.png` fixture |
| `image_live_smoke.py` | Direct CDP image upload and recognition with same fixture |
| `audio_http_live.py` | Speech with `/tmp/chatbridge_spoken_test.wav` fixture |
| `image_existing_live.py` | Re-download an existing generated image; requires `CHATBRIDGE_TEST_CONVERSATION_ID` |

Fixture-dependent tests should only be run after the operator prepares
their own synthetic fixtures. These scripts do not embed login tokens.

The scripts listed in the table use temporary SQLite databases or direct CDP.
Other scripts, including `http_smoke.py`, `stress11.py`,
`production_multimodal_smoke.py`, `cleanup_chats.py` and `continue_existing.py`,
connect to a running HTTP gateway and can modify its persistent state. Point
them only at an isolated test instance. `continue_existing.py` requires an
explicit session ID and prompt; preserve its printed client request ID when
retrying the same message.

A provider model may decline some requested file generation; tests should
display the actual stored file list and explicit `file_unavailable` events.
In particular, a mere sandbox link without downloadable bytes does not
constitute a successful transfer.
