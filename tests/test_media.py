"""Multimodal payload, persistence, binary GET and image-response contracts."""

import base64
import tempfile
from pathlib import Path

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.media import MediaError, parse_data_url, parse_message
from chatbridge.mock_transport import MockTransport
from chatbridge.model import TransportEvent
from chatbridge.server import make_app
from chatbridge.store import Store

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/k5sAAAAASUVORK5CYII="
)
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


@pytest.fixture
async def image_app():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db.sqlite")
        transport = MockTransport(delay=0.02)
        engine = GatewayEngine(store, transport)
        await engine.start()
        cfg = Settings(transport="mock")
        client = TestClient(TestServer(make_app(store, engine, cfg)))
        await client.start_server()
        yield client, store, engine
        await client.close()
        await engine.stop()
        store.close()


async def session(client):
    resp = await client.post("/v1/sessions", json={"title": "image-test"})
    return (await resp.json())["session_id"]


def test_image_input_validation_and_multimodal_parts():
    image = parse_data_url(DATA_URL, "test")
    assert image.mime_type == "image/png"
    assert image.name.endswith(".png")
    prompt, images = parse_message(
        {
            "content": [
                {"type": "text", "text": "What color?"},
                {"type": "image_url", "image_url": {"url": DATA_URL}},
            ]
        }
    )
    assert prompt == "What color?"
    assert len(images) == 1 and images[0].data == PNG
    with pytest.raises(MediaError):
        parse_data_url("https://localhost/private.png")
    with pytest.raises(MediaError):
        parse_data_url("data:image/jpeg;base64," + base64.b64encode(PNG).decode())
    with pytest.raises(MediaError):
        parse_message({"content": "", "images": []})


@pytest.mark.asyncio
async def test_native_json_images_and_binary_get(image_app):
    client, store, engine = image_app
    sid = await session(client)
    payload = {
        "content": "Describe this",
        "client_request_id": "image-01",
        "images": [{"filename": "test.png", "data_url": DATA_URL}],
    }
    resp = await client.post(f"/v1/sessions/{sid}/messages", json=payload)
    assert resp.status == 202
    sent = await resp.json()
    assert len(sent["images"]) == 1
    item = sent["images"][0]
    media_id = item["media_id"]
    assert item["mime_type"] == "image/png"
    retrieved = await client.get(f"/v1/media/{media_id}")
    assert retrieved.status == 200
    assert retrieved.content_type == "image/png"
    assert await retrieved.read() == PNG
    await engine.wait_terminal(sent["request_id"], 5)
    row = await (await client.get(f"/v1/requests/{sent['request_id']}")).json()
    assert row["status"] == "completed"
    assert row["images"][0]["media_id"] == media_id
    hist = await (await client.get(f"/v1/sessions/{sid}/messages")).json()
    assert len(hist["data"][0]["images"]) == 1
    again = await client.post(f"/v1/sessions/{sid}/messages", json=payload)
    assert again.status == 200
    assert (await again.json())["request_id"] == sent["request_id"]
    changed = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            **payload,
            "images": [{"data_url": DATA_URL, "filename": "test2"}],
        },
    )
    # Image filename changes do not affect logical idempotency.
    assert changed.status == 200


@pytest.mark.asyncio
async def test_multipart_images(image_app):
    client, store, engine = image_app
    sid = await session(client)
    data = FormData()
    data.add_field("content", "Hello photo")
    data.add_field("client_request_id", "upload-multi")
    data.add_field("images", PNG, filename="photo.png", content_type="image/png")
    response = await client.post(f"/v1/sessions/{sid}/messages", data=data)
    assert response.status == 202
    info = await response.json()
    await engine.wait_terminal(info["request_id"], 5)
    assert len(store.image_payloads(info["request_id"])) == 1


class ImageReplyTransport(MockTransport):
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
                    {"asset_pointer": "image-asset-test", "content_type": "image_asset_pointer"}
                ]
            },
        )
        yield TransportEvent(
            "image",
            meta={
                "filename": "assistant.png",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(PNG).decode(),
            },
        )
        yield TransportEvent("completed", message_id="33333333-3333-4333-8333-333333333333")


@pytest.mark.asyncio
async def test_assistant_image_reply_is_downloadable():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db.sqlite")
        engine = GatewayEngine(store, ImageReplyTransport())
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        try:
            sid = await session(client)
            response = await client.post(f"/v1/sessions/{sid}/messages", json={"content": "Show"})
            rid = (await response.json())["request_id"]
            await engine.wait_terminal(rid, 5)
            info = await (await client.get(f"/v1/requests/{rid}")).json()
            assert info["status"] == "completed"
            assert len(info["images"]) == 1
            assert len(info["image_refs"]) == 1
            m = await client.get("/v1/media/" + info["images"][0]["media_id"])
            assert await m.read() == PNG
            events = store.get_events(rid)
            assert "image" in [e["kind"] for e in events]
            assert "image_refs" in [e["kind"] for e in events]
        finally:
            await client.close()
            await engine.stop()
            store.close()


def test_turn_refs_include_tool_generated_images_not_previous_turn():
    from chatbridge.browser_transport import _turn_image_refs

    nodes = [
        {"role": "assistant", "id": "reply", "images": []},
        {
            "role": "tool",
            "id": "imagegen-result",
            "images": [
                {"asset_pointer": "sediment://file_test", "content_type": "image_asset_pointer"}
            ],
        },
        {
            "role": "tool",
            "id": "duplicate-tool",
            "images": [
                {"asset_pointer": "sediment://file_test", "content_type": "image_asset_pointer"}
            ],
        },
        {"role": "user", "id": "new-user", "content": "draw"},
        {
            "role": "tool",
            "id": "previous-tool",
            "images": [{"asset_pointer": "sediment://file_old"}],
        },
    ]
    result = _turn_image_refs(nodes, "new-user", "draw")
    assert len(result) == 1
    assert result[0]["asset_pointer"] == "sediment://file_test"
    assert _turn_image_refs(nodes, "different-user", "draw") == []


@pytest.mark.asyncio
async def test_image_only_request_and_invalid_mime(image_app):
    client, store, engine = image_app
    sid = await session(client)
    ok = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "content": "",
            "images": [{"data_url": DATA_URL}],
        },
    )
    assert ok.status == 202
    rid = (await ok.json())["request_id"]
    await engine.wait_terminal(rid, 5)
    assert store.get_request(rid)["status"] == "completed"
    bad = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "content": "Hello",
            "images": [{"data_base64": base64.b64encode(PNG).decode(), "mime_type": "image/jpeg"}],
        },
    )
    assert bad.status == 400


@pytest.mark.asyncio
async def test_openai_multimodal_format(image_app):
    client, store, engine = image_app
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What do you see?"},
                        {"type": "image_url", "image_url": {"url": DATA_URL}},
                    ],
                }
            ],
        },
    )
    assert response.status == 200
    item = await response.json()
    assert item["choices"][0]["message"]["content"] == "Echo: What do you see?"
    assert len(store.image_payloads(item["request_id"])) == 1


@pytest.mark.asyncio
async def test_changed_image_changes_idempotency_hash(image_app):
    client, store, engine = image_app
    sid = await session(client)
    a = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "client_request_id": "fixed",
            "content": "Same",
            "images": [{"data_url": DATA_URL}],
        },
    )
    assert a.status == 202
    changed = bytearray(PNG)
    changed[-7] ^= 1
    b = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={
            "client_request_id": "fixed",
            "content": "Same",
            "images": [{"data_base64": base64.b64encode(changed).decode()}],
        },
    )
    assert b.status == 409
