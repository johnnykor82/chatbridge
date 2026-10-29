"""Regression: multiple arbitrary binary outputs, MIME, and missing-file disclosure."""

import base64
import tempfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.media import MediaError, parse_message, verify_file
from chatbridge.mock_transport import MockTransport
from chatbridge.model import TransportEvent
from chatbridge.server import make_app
from chatbridge.store import Store

OUTPUT_FILES = [
    ("a.txt", "text/plain", b"Hello world\n"),
    ("b.csv", "text/csv", b"id,value\n1,2\n"),
    ("c.py", "text/x-python", b"print(42)\n"),
    ("d.pdf", "application/pdf", b"%PDF-1.4\n1 0 obj\n%%EOF"),
    (
        "e.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK\x03\x04document",
    ),
    ("f.odd", "application/octet-stream", b"\x00\x01\xff\x06"),
]


class MultiFileTransport(MockTransport):
    async def generate(
        self, *, session_id, conversation_id, prompt, request_id, images=None, reasoning_effort=None
    ):
        yield TransportEvent(
            "submitted",
            conversation_id=conversation_id or "11111111-1111-4111-8111-111111111111",
            message_id="22222222-2222-4222-8222-222222222222",
        )
        for filename, mimetype, body in OUTPUT_FILES:
            yield TransportEvent(
                "artifact",
                meta={
                    "filename": filename,
                    "mime_type": mimetype,
                    "data_base64": base64.b64encode(body).decode(),
                },
            )
        yield TransportEvent(
            "file_unavailable",
            meta={"filename": "broken.zip", "reason": "file_card_not_found"},
        )
        yield TransportEvent("completed", message_id="33333333-3333-4333-8333-333333333333")


@pytest.mark.asyncio
async def test_all_generated_file_types_downloadable_and_missing_file_reported():
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "db.sqlite")
        engine = GatewayEngine(store, MultiFileTransport())
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
            response = await client.post(
                f"/v1/sessions/{sid}/messages", json={"content": "Create multiple files"}
            )
            rid = (await response.json())["request_id"]
            await engine.wait_terminal(rid, timeout=4)
            result = await (await client.get("/v1/requests/" + rid)).json()
            assert result["status"] == "completed"
            assert len(result["attachments"]) == len(OUTPUT_FILES)
            assert result["attachments_complete"] is False
            assert len(result["attachment_errors"]) == 1
            assert result["attachment_errors"][0]["filename"] == "broken.zip"
            assert sum(ev["kind"] == "artifact" for ev in store.get_events(rid)) == len(
                OUTPUT_FILES
            )
            for filename, mime, data in OUTPUT_FILES:
                asset = next(a for a in result["attachments"] if a["filename"] == filename)
                returned = await client.get("/v1/media/" + asset["media_id"])
                assert returned.status == 200
                assert returned.content_type == mime
                assert await returned.read() == data
                assert returned.headers["Content-Disposition"].startswith("attachment;")
                assert asset["sha256"] == verify_file(data, filename, mime).digest
        finally:
            await client.close()
            await engine.stop()
            store.close()


def test_accept_arbitrary_extensions_as_data_not_code_execution():
    script = verify_file(b"#!/bin/sh\necho hello\n", "backup.sh", "text/x-shellscript")
    assert script.data.startswith(b"#!")
    assert script.name == "backup.sh"
    out, attachments = parse_message(
        {
            "content": "Describe these",
            "attachments": [
                {
                    "filename": "config.mycustomextension",
                    "data_base64": base64.b64encode(b"arbitrary bytes").decode(),
                }
            ],
        }
    )
    assert out == "Describe these"
    assert len(attachments) == 1
    assert attachments[0].data == b"arbitrary bytes"
    with pytest.raises(MediaError):
        verify_file(b"wrong", "fake.pdf", "application/pdf")


def test_sandbox_file_recognition():
    from chatbridge.artifact_export import SANDBOX_FILE

    text = (
        "[One](sandbox:/mnt/data/report.pdf) "
        "[Two](sandbox:/mnt/data/source.py) "
        "[Three](sandbox:/mnt/data/Word%20document.docx)"
    )
    assert SANDBOX_FILE.findall(text) == ["report.pdf", "source.py", "Word%20document.docx"]
