"""Additional contract tests for OpenAI compatibility, history and MCP audit."""

import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.model import TransportEvent
from chatbridge.server import make_app
from chatbridge.store import Store


@pytest.fixture
async def app_fixture():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "state.db")
        tr = MockTransport(delay=0.05)
        cfg = replace(Settings(), database=Path(folder) / "state.db", transport="mock")
        engine = GatewayEngine(store, tr)
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, cfg)))
        await client.start_server()
        yield client, store, engine, tr
        await client.close()
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_openai_json(app_fixture):
    client, store, engine, tr = app_fixture
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert response.status == 200
    data = await response.json()
    assert data["choices"][0]["message"]["content"] == "Echo: Hello"
    assert data["session_id"]
    assert data["conversation_id"]
    assert store.count("sessions") == 1


@pytest.mark.asyncio
async def test_openai_sse(app_fixture):
    client, store, engine, tr = app_fixture
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "stream me"}],
        },
    )
    assert response.status == 200
    text = await response.text()
    assert "chat.completion.chunk" in text
    assert "Echo: stream me" in text
    assert "[DONE]" in text


@pytest.mark.asyncio
async def test_imported_session_reused(app_fixture):
    client, store, engine, tr = app_fixture
    conversation = "11111111-1111-4111-8111-111111111111"
    one = await client.post("/v1/sessions", json={"url": "https://chatgpt.com/c/" + conversation})
    two = await client.post("/v1/sessions", json={"conversation_id": conversation})
    a, b = await one.json(), await two.json()
    assert a["session_id"] == b["session_id"]
    assert a["conversation_id"] == conversation


class ApprovalSequenceTransport(MockTransport):
    async def generate(self, *, session_id, conversation_id, prompt, request_id):
        yield TransportEvent(
            "submitted",
            conversation_id=conversation_id or "11111111-1111-4111-8111-111111111111",
            message_id="11111111-2222-4111-8111-111111111111",
        )
        yield TransportEvent("tool_running", tool="Mac Control", operation="terminal_exec")
        yield TransportEvent("awaiting_approval", tool="Mac Control", operation="terminal_exec")
        yield TransportEvent(
            "approval_applied",
            tool="Mac Control",
            operation="terminal_exec",
            scope="conversation",
            meta={"result": "clicked"},
        )
        yield TransportEvent("text_delta", content="Partial")
        await asyncio.sleep(0.02)
        yield TransportEvent("tool_running", tool="Mac Control", operation="system_info")
        yield TransportEvent("text_replace", content="Correct final")
        yield TransportEvent("completed", message_id="11111111-3333-4111-8111-111111111111")


@pytest.mark.asyncio
async def test_tool_cycle_requires_final_and_audits():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db")
        transport = ApprovalSequenceTransport()
        engine = GatewayEngine(store, transport)
        await engine.start()
        sid = store.create_session()["session_id"]
        row, _ = engine.enqueue(sid, "test", "one")
        await engine.wait_terminal(row["request_id"], timeout=5)
        final = store.get_request(row["request_id"])
        assert final["status"] == "completed"
        assert final["answer"] == "Correct final"
        assert store.count("approvals") == 1
        events = store.get_events(row["request_id"])
        types = [e["kind"] for e in events]
        assert "approval_applied" in types
        assert types.index("completed") > types.index("approval_applied")
        assert "text_replace" in types
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_no_automatic_retry_on_failure_after_sent():
    class BreakTransport(MockTransport):
        async def generate(self, *, session_id, conversation_id, prompt, request_id):
            yield TransportEvent(
                "submitted",
                conversation_id="11111111-1111-4111-8111-111111111111",
                message_id="11111111-2222-4111-8111-111111111111",
            )
            raise RuntimeError("test disconnect")
            yield

    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db")
        engine = GatewayEngine(store, BreakTransport())
        await engine.start()
        sid = store.create_session()["session_id"]
        row, _ = engine.enqueue(sid, "hello", "retry")
        await engine.wait_terminal(row["request_id"], timeout=5)
        assert store.get_request(row["request_id"])["status"] == "unknown"
        await engine.stop()
        store.close()
