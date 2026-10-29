"""Offline integration: HTTP API, persistent queue, SSE, idempotency, parallelism."""

import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store


@pytest.fixture
async def service():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "gateway.db")
        transport = MockTransport(delay=0.12)
        config = replace(
            Settings(),
            database=Path(folder) / "gateway.db",
            max_active_sessions=10,
            transport="mock",
        )
        engine = GatewayEngine(store, transport, 10)
        await engine.start()
        app = make_app(store, engine, config)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        yield client, store, engine, transport
        await client.close()
        await engine.stop()
        store.close()


async def new_session(client, title=""):
    resp = await client.post("/v1/sessions", json={"title": title})
    assert resp.status == 201
    return await resp.json()


async def send(client, sid, prompt, key=None):
    return await client.post(
        f"/v1/sessions/{sid}/messages", json={"content": prompt, "client_request_id": key}
    )


@pytest.mark.asyncio
async def test_basic_session_and_history(service):
    client, store, engine, transport = service
    sess = await new_session(client)
    assert sess["session_id"] and sess["conversation_id"] is None
    response = await send(client, sess["session_id"], "hello", "key1")
    assert response.status == 202
    info = await response.json()
    assert info["request_id"]
    await engine.wait_terminal(info["request_id"], timeout=4)
    result = await (await client.get(info["status_url"])).json()
    assert result["status"] == "completed"
    assert result["answer"] == "Echo: hello"
    first = await (await client.get(f"/v1/sessions/{sess['session_id']}")).json()
    assert first["conversation_id"] is not None
    hist = await (await client.get(f"/v1/sessions/{sess['session_id']}/messages")).json()
    assert [m["role"] for m in hist["data"]] == ["user", "assistant"]
    sessions = await (await client.get("/v1/sessions")).json()
    assert len(sessions["data"]) == 1


@pytest.mark.asyncio
async def test_idempotence(service):
    client, store, engine, transport = service
    s = await new_session(client)
    sid = s["session_id"]
    a = await (await send(client, sid, "one", "dupe")).json()
    b = await (await send(client, sid, "one", "dupe")).json()
    assert a["request_id"] == b["request_id"]
    c = await send(client, sid, "other", "dupe")
    assert c.status == 409
    await engine.wait_terminal(a["request_id"], 4)
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_ten_parallel_and_eleventh_queued(service):
    client, store, engine, transport = service
    sessions = [await new_session(client) for _ in range(11)]
    requests = []
    for i, s in enumerate(sessions):
        response = await send(client, s["session_id"], "marker-" + str(i), "k-" + str(i))
        requests.append((await response.json())["request_id"])
    # The first 10 should be executing, the 11th must wait.
    await asyncio.sleep(0.025)
    assert engine.active == 10
    assert store.get_request(requests[-1])["status"] == "queued"
    await asyncio.gather(*(engine.wait_terminal(rid, 4) for rid in requests))
    assert engine.max_seen == 10
    assert transport.max_seen == 10
    for i, rid in enumerate(requests):
        assert store.get_request(rid)["answer"] == "Echo: marker-" + str(i)


@pytest.mark.asyncio
async def test_same_session_serialization(service):
    client, store, engine, transport = service
    sess = await new_session(client)
    s = sess["session_id"]
    a = (await (await send(client, s, "A", "a")).json())["request_id"]
    b = (await (await send(client, s, "B", "b")).json())["request_id"]
    await asyncio.gather(engine.wait_terminal(a, 4), engine.wait_terminal(b, 4))
    assert engine.max_seen == 1
    assert store.get_request(a)["status"] == "completed"
    assert store.get_request(b)["status"] == "completed"


@pytest.mark.asyncio
async def test_sse_replay(service):
    client, store, engine, transport = service
    sess = await new_session(client)
    info = await (await send(client, sess["session_id"], "SSE", "sse")).json()
    await engine.wait_terminal(info["request_id"], 4)
    response = await client.get(info["events_url"])
    assert response.status == 200
    txt = await response.text()
    assert "event: completed" in txt and "event: text_delta" in txt
    events = store.get_events(info["request_id"])
    after = events[1]["event_id"]
    replay = await client.get(info["events_url"], headers={"Last-Event-ID": str(after)})
    next_text = await replay.text()
    assert f"id: {after}\\n" not in next_text
    assert "event: completed" in next_text


@pytest.mark.asyncio
async def test_restart_reconciles_unfinished():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "state.db"
        store = Store(path)
        sess = store.create_session()
        row, _ = store.enqueue(session_id=sess["session_id"], prompt="X", client_request_id="x")
        store.set_status(row["request_id"], "tool_running")
        store.close()
        store = Store(path)
        engine = GatewayEngine(store, MockTransport(), max_active=10)
        await engine.start()
        assert store.get_request(row["request_id"])["status"] == "unknown"
        assert engine.active == 0
        await engine.stop()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://untrusted.example", "null", "http://localhost:1"])
async def test_cross_origin_requests_rejected_without_key(service, origin):
    client, store, engine, transport = service
    before = len(store.list_sessions())
    response = await client.post("/v1/sessions", json={}, headers={"Origin": origin})
    assert response.status == 403
    assert len(store.list_sessions()) == before
    assert (await client.get("/health", headers={"Origin": origin})).status == 403


@pytest.mark.asyncio
async def test_same_origin_and_native_clients_work_without_key(service):
    client, store, engine, transport = service
    origin = str(client.make_url("/")).rstrip("/")
    assert (await client.get("/health", headers={"Origin": origin})).status == 200
    assert (await client.post("/v1/sessions", json={})).status == 201


@pytest.mark.asyncio
async def test_duplicate_origin_rejected(service):
    client, store, engine, transport = service
    origin = str(client.make_url("/")).rstrip("/")
    response = await client.get("/health", headers=[("Origin", origin), ("Origin", origin)])
    assert response.status == 403


@pytest.mark.asyncio
async def test_keyless_loopback_rejects_rebinding_host(service):
    client, store, engine, transport = service
    headers = {"Host": "attacker.example:8787", "Origin": "http://attacker.example:8787"}
    assert (await client.get("/health", headers=headers)).status == 403
    assert (await client.get("/health", headers={"Host": "attacker.example:8787"})).status == 403


@pytest.mark.asyncio
async def test_api_key_still_required_and_domain_host_allowed(tmp_path):
    store = Store(tmp_path / "gateway.db")
    config = replace(Settings(), api_key="test-key", host="0.0.0.0", transport="mock")
    engine = GatewayEngine(store, MockTransport(delay=0.01), 1)
    client = TestClient(TestServer(make_app(store, engine, config)))
    await engine.start()
    await client.start_server()
    try:
        headers = {"Host": "gateway.example:8787"}
        assert (await client.get("/health", headers=headers)).status == 401
        headers["Authorization"] = "Bearer test-key"
        assert (await client.get("/health", headers=headers)).status == 200
        headers["Origin"] = "http://gateway.example:8787"
        assert (await client.get("/health", headers=headers)).status == 200
        headers["Origin"] = "https://untrusted.example"
        assert (await client.get("/health", headers=headers)).status == 403
    finally:
        await client.close()
        await engine.stop()
        store.close()
