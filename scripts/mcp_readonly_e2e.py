"""Live ChatBridge MCP read-only approval test; dedicated temporary session.

Checks real Mac Control custom_query(system_info) and approval audit without
running terminal commands or modifying the user's environment.
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

from chatbridge.browser_transport import BrowserTransport
from chatbridge.engine import GatewayEngine
from chatbridge.store import Store

PROMPT = (
    "Проведи проверку доступности Mac Control. Вызови подключённый инструмент "
    "Mac Control custom_query c operation='system_info' и сообщи "
    "только факт получения результата и тип компьютера. "
    "Не запускай терминал, не изменяй файлы."
)

async def main():
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp) / "check.db")
        transport = BrowserTransport(
            "http://127.0.0.1:9335",
            hard_timeout_seconds=105,
            verify_interval_seconds=2,
        )
        engine = GatewayEngine(store, transport)
        await engine.start()
        sid = store.create_session(title="gateway-real-readonly-mcp")["session_id"]
        request, _ = engine.enqueue(sid, PROMPT, "read-only-approval-live")
        rid = request["request_id"]
        print(json.dumps({"phase": "started", "session_id": sid, "request_id": rid}), flush=True)
        started = time.monotonic()
        previous = None
        for _ in range(120):
            row = store.get_request(rid)
            if row["status"] != previous:
                print(json.dumps({"phase": "status", "state": row["status"],
                                  "seconds": round(time.monotonic()-started,1)},ensure_ascii=False),flush=True)
                previous = row["status"]
            if row["status"] in ("completed","unknown","failed","interrupted"):
                break
            await asyncio.sleep(1)
        row = store.get_request(rid)
        logs = [dict(record) for record in store.db.execute(
            "SELECT scope,result,tool,operation FROM approvals"
        )]
        print(json.dumps({
            "phase": "final",
            "state": row["status"],
            "answer": row["answer"][:500],
            "error": row["last_error"],
            "approvals": logs,
            "events": [e["kind"] for e in store.get_events(rid)]
        },ensure_ascii=False),flush=True)
        await engine.stop()
        store.close()

asyncio.run(main())
