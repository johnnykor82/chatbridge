"""Multi-asset message and reasoning quality contracts."""

import base64
import tempfile
import wave
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

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/k5sAAAAASUVORK5CYII="
)


def data64(raw):
    return base64.b64encode(raw).decode()


def wav():
    import io

    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setframerate(8000)
        f.setnchannels(1)
        f.setsampwidth(2)
        f.writeframes(b"\0\0" * 1000)
    return out.getvalue()


@pytest.fixture
async def api():
    with tempfile.TemporaryDirectory() as path:
        store = Store(Path(path) / "api.sqlite")
        tr = MockTransport(delay=0.01)
        engine = GatewayEngine(store, tr)
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        yield client, store, engine
        await client.close()
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_two_input_images_saved(api):
    client, store, engine = api
    sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
    q = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "content": "Compare photos",
            "images": [
                {"filename": "one.png", "data_base64": data64(PNG), "mime_type": "image/png"},
                {"filename": "two.png", "data_base64": data64(PNG), "mime_type": "image/png"},
            ],
        },
    )
    assert q.status == 202
    rid = (await q.json())["request_id"]
    await engine.wait_terminal(rid, 4)
    data = store.list_media(rid, "user")
    assert len(data) == 2
    assert data[0]["media_id"] != data[1]["media_id"]


@pytest.mark.asyncio
async def test_audio_and_pdf_attachments(api):
    client, store, engine = api
    sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
    content = {
        "content": "Analyze sound and document",
        "attachments": [
            {"filename": "memo.wav", "data_base64": data64(wav()), "mime_type": "audio/wav"},
            {
                "filename": "agenda.pdf",
                "data_base64": data64(b"%PDF-1.7\n\n%%EOF"),
                "mime_type": "application/pdf",
            },
        ],
    }
    resp = await client.post(f"/v1/sessions/{sid}/messages", json=content)
    assert resp.status == 202
    rid = (await resp.json())["request_id"]
    await engine.wait_terminal(rid, 4)
    row = await (await client.get("/v1/requests/" + rid)).json()
    assert sorted(i["mime_type"] for i in row["attachments"]) == ["application/pdf", "audio/wav"]
    for i in row["attachments"]:
        obj = await client.get("/v1/media/" + i["media_id"])
        assert obj.status == 200
        assert len(await obj.read()) > 0


def test_invalid_pdf_and_wav_rejected():
    with pytest.raises(MediaError):
        verify_file(b"not a PDF", "report.pdf", "application/pdf")
    with pytest.raises(MediaError):
        verify_file(b"not a WAV", "memo.wav", "audio/wav")
    unknown = verify_file(b"custom file data", "example.custom", "")
    assert unknown.mime_type == "application/octet-stream"
    content, parts = parse_message(
        {
            "content": [
                {"type": "text", "text": "hello"},
                {
                    "type": "file",
                    "file": {
                        "filename": "memo.wav",
                        "mime_type": "audio/wav",
                        "data_base64": data64(wav()),
                    },
                },
            ]
        }
    )
    assert content == "hello" and len(parts) == 1


class TwoImageToolTransport(MockTransport):
    async def generate(self, *, session_id, conversation_id, prompt, request_id, images=None):
        yield TransportEvent(
            "submitted",
            conversation_id=conversation_id or "11111111-1111-4111-8111-111111111111",
            message_id="22222222-2222-4222-8222-222222222222",
        )
        yield TransportEvent(
            "image_refs",
            meta={
                "refs": [
                    {"asset_pointer": "sediment://file_one", "mime_type": "image/png"},
                    {"asset_pointer": "sediment://file_two", "mime_type": "image/png"},
                ]
            },
        )
        for i in range(2):
            yield TransportEvent(
                "image",
                meta={
                    "filename": f"result-{i}.png",
                    "mime_type": "image/png",
                    "data_base64": data64(PNG),
                },
            )
        yield TransportEvent("completed", message_id="33333333-3333-4333-8333-333333333333")


@pytest.mark.asyncio
async def test_two_independent_outgoing_image_files():
    with tempfile.TemporaryDirectory() as path:
        store = Store(Path(path) / "db")
        engine = GatewayEngine(store, TwoImageToolTransport())
        await engine.start()
        sid = store.create_session()["session_id"]
        req, _ = engine.enqueue(sid, "Create two images")
        await engine.wait_terminal(req["request_id"], 4)
        files = store.list_media(req["request_id"], "assistant")
        refs = store.list_image_refs(req["request_id"], "assistant")
        assert len(files) == 2 and len(refs) == 2
        assert len({i["media_id"] for i in files}) == 2
        assert store.get_request(req["request_id"])["status"] == "completed"
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_thinking_effort_request_and_validation(api):
    client, store, engine = api
    sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
    good = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "content": "Hello",
            "reasoning_effort": "medium",
            "client_request_id": "effort-1",
        },
    )
    assert good.status == 202
    data = await good.json()
    assert data["reasoning_effort"] == "medium"
    assert store.get_request(data["request_id"])["reasoning_effort"] == "medium"
    bad = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "content": "Other",
            "reasoning_effort": "extreme",
        },
    )
    assert bad.status == 400


def test_default_high_and_old_sqlite_database_migration():
    assert Settings().reasoning_effort == "high"
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "old.sqlite"
        import sqlite3

        c = sqlite3.connect(db)
        c.execute(
            "CREATE TABLE requests (request_id TEXT PRIMARY KEY, session_id TEXT, client_request_id TEXT, "
            "payload_hash TEXT, prompt TEXT, status TEXT, answer TEXT, last_error TEXT, "
            "user_message_id TEXT, assistant_message_id TEXT, created_at REAL, updated_at REAL)"
        )
        c.commit()
        c.close()
        store = Store(db)
        assert "reasoning_effort" in {r[1] for r in store.db.execute("PRAGMA table_info(requests)")}
        store.close()
