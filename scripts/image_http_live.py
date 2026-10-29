"""Opt-in live native HTTP image upload test in an isolated temporary database."""
import asyncio
import os
import base64
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
    raw = Path("/tmp/chatbridge_multimodal_probe.png").read_bytes()
    with tempfile.TemporaryDirectory(prefix="chatbridge-image-http-") as root:
        store = Store(Path(root) / "image-live.sqlite")
        tr = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"), hard_timeout_seconds=120)
        engine = GatewayEngine(store, tr)
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"))))
        await client.start_server()
        try:
            res = await client.post("/v1/sessions", json={"title": "native image HTTP live"})
            sid = (await res.json())["session_id"]
            sent = await client.post(f"/v1/sessions/{sid}/messages", json={
                "content": "Назови две фигуры и их цвета на приложенной картинке. Кратко.",
                "client_request_id": "image-json-http-live",
                "images": [{
                    "filename": "shapes.png",
                    "mime_type": "image/png",
                    "data_base64": base64.b64encode(raw).decode(),
                }],
            })
            result = await sent.json()
            rid = result["request_id"]
            await engine.wait_terminal(rid, 135)
            info = store.get_request(rid)
            media = store.list_media(rid, "user")
            downloaded = await client.get("/v1/media/" + media[0]["media_id"])
            actual = await downloaded.read()
            print(json.dumps({
                "request_state": info["status"],
                "answer": info["answer"][:250],
                "vision_correct": ("красн" in info["answer"].lower() and
                                   "син" in info["answer"].lower()),
                "upload_http_status": sent.status,
                "download_http_status": downloaded.status,
                "bytes_verified": actual == raw,
                "stored_images": len(media),
                "conversation_id": store.get_session(sid)["conversation_id"],
                "error": info["last_error"],
            }, ensure_ascii=False), flush=True)
        finally:
            await client.close()
            await engine.stop()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
