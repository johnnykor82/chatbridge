"""Live HTTP end-to-end test of receiving a newly generated ChatGPT image.

Uses isolated SQLite and TestServer. Generates only one simple image.
"""
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
    with tempfile.TemporaryDirectory(prefix="chatbridge-image-response-") as root:
        store = Store(Path(root) / "live.sqlite")
        transport = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"), hard_timeout_seconds=215)
        engine = GatewayEngine(store, transport)
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={
                "title": "live generated image via HTTP"
            })).json())["session_id"]
            response = await client.post(f"/v1/sessions/{sid}/messages", json={
                "content": "Создай картинку: одна красная звезда на белом фоне. "
                           "Нужно именно сгенерированное изображение, не текст и не SVG.",
                "client_request_id": "new-generated-image-http-live",
            })
            request = await response.json()
            rid = request["request_id"]
            print("SUBMITTED", rid, flush=True)
            try:
                await engine.wait_terminal(rid, timeout=228)
            except asyncio.TimeoutError:
                pass
            status = await (await client.get("/v1/requests/" + rid)).json()
            images = [image for image in status["images"] if image["role"] == "assistant"]
            result = {
                "status": status["status"],
                "answer": status["answer"][:150],
                "image_count": len(images),
                "ref_count": len(status["image_refs"]),
                "error": status["last_error"],
            }
            if images:
                downloaded = await client.get("/v1/media/" + images[0]["media_id"])
                data = await downloaded.read()
                result.update({
                    "download_status": downloaded.status,
                    "mime_type": downloaded.content_type,
                    "size": len(data),
                    "valid_image": data.startswith(b"\x89PNG\r\n\x1a\n") or
                                   data.startswith(b"\xff\xd8\xff"),
                })
            print("RESULT", json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            await client.close()
            await engine.stop()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
