"""Real CDP + HTTP E2E: four different generated downloadable file types."""
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
    with tempfile.TemporaryDirectory(prefix="chatbridge-office-files-") as folder:
        store = Store(Path(folder) / "state.db")
        transport = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"), hard_timeout_seconds=230)
        engine = GatewayEngine(store, transport)
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={
                "title": "three office file types test"
            })).json())["session_id"]
            prompt = (
                "Создай ТРИ ОТДЕЛЬНЫХ файла для скачивания: "
                "document_example.docx с фразой Пример документа Word, "
                "workbook_example.xlsx с таблицей A1=Название, A2=Тест, "
                "archive_example.zip с одним файлом hello.txt. "
                "Именно сохрани три файла и дай возможность скачать их, не только текст."
            )
            res = await client.post(f"/v1/sessions/{sid}/messages", json={
                "content": prompt, "client_request_id": "three-office-files-20261025"
            })
            job = await res.json()
            print("STARTED", job["request_id"], flush=True)
            try:
                await engine.wait_terminal(job["request_id"], timeout=238)
            except asyncio.TimeoutError:
                pass
            rid = job["request_id"]
            request = (await (await client.get("/v1/requests/" + rid)).json())
            files = [x for x in request["attachments"] if x["role"] == "assistant"]
            downloaded = []
            for info in files:
                response = await client.get("/v1/media/" + info["media_id"])
                raw = await response.read()
                downloaded.append({
                    "filename": info["filename"],
                    "mime_type": info["mime_type"],
                    "status": response.status,
                    "size": len(raw),
                    "matches_stored_bytes": raw == store.get_media(info["media_id"])["content"],
                    "pdf_signature": raw.startswith(b"%PDF-"),
                })
            print("RESULT", json.dumps({
                "status": request["status"],
                "count": len(downloaded),
                "files": downloaded,
                "complete": request.get("attachments_complete"),
                "errors": request.get("attachment_errors"),
                "answer": request["answer"][:300],
                "error": request["last_error"],
            }, ensure_ascii=False), flush=True)
        finally:
            await client.close()
            await engine.stop()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
