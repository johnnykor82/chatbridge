"""Effective reasoning level: selectable per request, high by default, durable."""

import tempfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store


@pytest.mark.asyncio
async def test_default_high_and_explicit_medium_are_stored_and_reported():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db.sqlite")
        engine = GatewayEngine(store, MockTransport(delay=0.01))
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
            for payload, expected in (
                ({"content": "First", "client_request_id": "effort-high-default"}, "high"),
                (
                    {
                        "content": "Second",
                        "client_request_id": "effort-medium",
                        "reasoning_effort": "medium",
                    },
                    "medium",
                ),
            ):
                response = await client.post(f"/v1/sessions/{sid}/messages", json=payload)
                assert response.status == 202
                initial = await response.json()
                assert initial["reasoning_effort"] == expected
                assert store.get_request(initial["request_id"])["reasoning_effort"] == expected
                await engine.wait_terminal(initial["request_id"], 5)
                fetched = await (await client.get("/v1/requests/" + initial["request_id"])).json()
                assert fetched["status"] == "completed"
                assert fetched["reasoning_effort"] == expected

            info = await (await client.get("/v1/system/status")).json()
            assert info["default_reasoning_effort"] == "high"
            assert info["supported_reasoning_efforts"] == ["low", "medium", "high"]
        finally:
            await client.close()
            await engine.stop()
            store.close()


@pytest.mark.asyncio
async def test_different_effort_with_same_idempotency_key_returns_conflict():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db.sqlite")
        engine = GatewayEngine(store, MockTransport(delay=0.01))
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
            url = f"/v1/sessions/{sid}/messages"
            first = await client.post(
                url,
                json={
                    "content": "Same prompt",
                    "client_request_id": "same",
                    "reasoning_effort": "high",
                },
            )
            assert first.status == 202
            first_id = (await first.json())["request_id"]
            repeat = await client.post(
                url, json={"content": "Same prompt", "client_request_id": "same"}
            )
            assert repeat.status == 200
            assert (await repeat.json())["request_id"] == first_id
            different = await client.post(
                url,
                json={
                    "content": "Same prompt",
                    "client_request_id": "same",
                    "reasoning_effort": "low",
                },
            )
            assert different.status == 409
        finally:
            await client.close()
            await engine.stop()
            store.close()


def test_old_null_effort_rows_are_high_for_idempotency():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db.sqlite")
        sid = store.create_session()["session_id"]
        row, _ = store.enqueue(session_id=sid, prompt="Text", client_request_id="old")
        store.db.execute(
            "UPDATE requests SET reasoning_effort=NULL WHERE request_id=?", (row["request_id"],)
        )
        store.db.commit()
        existing, created = store.enqueue(
            session_id=sid, prompt="Text", client_request_id="old", reasoning_effort="high"
        )
        assert not created
        assert existing["request_id"] == row["request_id"]
        store.close()


@pytest.mark.asyncio
async def test_default_can_be_set_to_medium_for_nondefault_installs():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db.sqlite")
        engine = GatewayEngine(store, MockTransport(), default_reasoning_effort="medium")
        await engine.start()
        sid = store.create_session()["session_id"]
        row, _ = engine.enqueue(sid, "Text", "default-medium")
        assert row["reasoning_effort"] == "medium"
        assert engine.default_reasoning_effort == "medium"
        await engine.wait_terminal(row["request_id"], 5)
        await engine.stop()
        store.close()
