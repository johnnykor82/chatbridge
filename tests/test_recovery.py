"""Recovery/reconciliation preserves IDs and does not replay uncertain turns."""

import tempfile
from pathlib import Path

import pytest

from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.store import Store


class HistoryTransport(MockTransport):
    async def read_conversation(self, conversation_id):
        return {
            "conversation_id": conversation_id,
            "chain": [
                {
                    "role": "assistant",
                    "id": "assistant-final",
                    "end_turn": True,
                    "status": "finished_successfully",
                    "content": "Recovered answer",
                    "parent_id": "user-old",
                },
                {
                    "role": "user",
                    "id": "user-old",
                    "end_turn": False,
                    "status": "finished_successfully",
                    "content": "task",
                    "parent_id": None,
                },
            ],
        }


@pytest.mark.asyncio
async def test_restart_reconciles_final_answer_without_replaying():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "state.db")
        sid = store.create_session(conversation_id="11111111-1111-4111-8111-111111111111")[
            "session_id"
        ]
        row, _ = store.enqueue(session_id=sid, prompt="task", client_request_id="key")
        rid = row["request_id"]
        store.set_ids(rid, user_message_id="user-old")
        store.set_status(rid, "tool_running")
        store.close()
        store = Store(Path(folder) / "state.db")
        tr = HistoryTransport()
        engine = GatewayEngine(store, tr)
        await engine.start()
        if engine._reconcile_task:
            await engine._reconcile_task
        final = store.get_request(rid)
        assert final["status"] == "completed"
        assert final["answer"] == "Recovered answer"
        assert final["assistant_message_id"] == "assistant-final"
        assert tr.calls == []
        assert store.count("events") == 1
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_unknown_without_verified_user_id_stays_unknown():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "db")
        sid = store.create_session()["session_id"]
        row, _ = store.enqueue(session_id=sid, prompt="task", client_request_id="one")
        store.set_status(row["request_id"], "unknown")
        tr = HistoryTransport()
        engine = GatewayEngine(store, tr)
        await engine.start()
        if engine._reconcile_task:
            await engine._reconcile_task
        assert store.get_request(row["request_id"])["status"] == "unknown"
        assert tr.calls == []
        await engine.stop()
        store.close()
