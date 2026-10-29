"""Watcher API contract for a provider-confirmed latest turn."""

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store

CID = "11111111-2222-4333-8444-555555555555"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node,expected",
    [
        (
            {
                "id": "final",
                "role": "assistant",
                "end_turn": True,
                "status": "finished_successfully",
                "content": "Done",
            },
            True,
        ),
        (
            {
                "id": "streaming",
                "role": "assistant",
                "end_turn": False,
                "status": "in_progress",
                "content": "Partial",
            },
            False,
        ),
        (
            {
                "id": "tool",
                "role": "tool",
                "end_turn": True,
                "status": "finished_successfully",
                "content": "tool output",
            },
            False,
        ),
        (
            {
                "id": "user",
                "role": "user",
                "end_turn": False,
                "status": "finished_successfully",
                "content": "question",
            },
            False,
        ),
    ],
)
async def test_turn_state_does_not_report_false_completion(
    tmp_path: Path, monkeypatch, node, expected
):
    store = Store(tmp_path / "state.db")
    session = store.create_session(conversation_id=CID)
    transport = MockTransport()

    async def history(cid):
        assert cid == CID
        return {"chain": [node]}

    monkeypatch.setattr(transport, "read_conversation", history, raising=False)
    engine = GatewayEngine(store, transport)
    await engine.start()
    client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
    await client.start_server()
    try:
        response = await client.get(f"/v1/sessions/{session['session_id']}/turn-state")
        assert response.status == 200
        body = await response.json()
        assert body["completed"] is expected
        assert body["generating"] is (not expected)
        assert body["content"] == ("Done" if expected else "")
        assert body["last_message_id"] == node["id"]
    finally:
        await client.close()
        await engine.stop()
        store.close()
