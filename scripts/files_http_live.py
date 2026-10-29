"""Live end-to-end: ChatGPT returns a CSV and Python script in one answer."""
import asyncio
import os
import json
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from chatbridge.browser_transport import BrowserTransport
from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.server import make_app
from chatbridge.store import Store


async def main():
    with tempfile.TemporaryDirectory(prefix="chatbridge-files-e2e-") as root:
        store = Store(Path(root) / "state.db")
        transport = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"), hard_timeout_seconds=220)
        engine = GatewayEngine(store, transport)
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={
                "title": "two-files-live-e2e"
            })).json())["session_id"]
            r = await client.post(f"/v1/sessions/{sid}/messages", json={
                "content": (
                    "Создай ДВА файла для скачивания: data_sample.csv с текстом 'id,value\\n1,42\\n' "
                    "и code_sample.py с текстом 'print(42)\\n'. "
                    "Пришли оба файла отдельными вложениями, а не только кодом в чате."
                ),
                "client_request_id": "two-files-live-20261025",
            })
            info = await r.json()
            rid = info["request_id"]
            print("SUBMITTED", rid, flush=True)
            try:
                await engine.wait_terminal(rid, timeout=230)
            except asyncio.TimeoutError:
                pass
            row = (await (await client.get(f"/v1/requests/{rid}")).json())
            assets = [x for x in row["attachments"] if x["role"] == "assistant"]
            results = []
            for asset in assets:
                resp = await client.get("/v1/media/" + asset["media_id"])
                data = await resp.read()
                results.append({
                    "name": asset["filename"],
                    "mime": asset["mime_type"],
                    "status": resp.status,
                    "length": len(data),
                    "text": repr(data[:90])
                })
            print("RESULT", json.dumps({
                "state": row["status"],
                "attachments": results,
                "count": len(assets),
                "refs": len(row["artifact_refs"]),
                "response": row["answer"][:300],
                "error": row["last_error"],
                "conversation_id": row["conversation_id"],
            }, ensure_ascii=False), flush=True)
        finally:
            await client.close()
            await engine.stop()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
