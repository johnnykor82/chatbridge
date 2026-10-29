"""Real ChatGPT image download without generating an additional image.

Uses a previous isolated generation smoke-test conversation. Fetches only the
image it produced, validates it and serves it via the gateway HTTP endpoint.
"""
import asyncio
import json
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from chatbridge.browser_transport import BACKEND_JS, DOWNLOAD_ASSET_JS, _turn_image_refs
from chatbridge.browser_transport import BrowserTransport
from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.media import verify_image
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store

import os

CONVERSATION_ID = os.getenv("CHATBRIDGE_TEST_CONVERSATION_ID")
if not CONVERSATION_ID:
    raise SystemExit("Set CHATBRIDGE_TEST_CONVERSATION_ID to an owned test conversation ID")


async def main():
    transport = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"))
    browser = await transport._ensure()
    page = await browser.contexts[0].new_page()
    try:
        await page.goto(f"https://chatgpt.com/c/{CONVERSATION_ID}",
                        wait_until="domcontentloaded")
        history = await page.evaluate(BACKEND_JS, CONVERSATION_ID)
        user = next(node for node in history["chain"] if node["role"] == "user")
        refs = _turn_image_refs(history["chain"], user["id"], user["content"])
        if not refs:
            raise RuntimeError("No image references found in the generated reply")
        images = []
        for ref in refs:
            payload = await page.evaluate(
                DOWNLOAD_ASSET_JS,
                {"cid": CONVERSATION_ID, "pointer": ref["asset_pointer"]},
            )
            if "error" in payload:
                raise RuntimeError(f"Image download failed: {payload['error']}")
            import base64
            image = verify_image(base64.b64decode(payload["data_base64"]),
                                 payload.get("filename", "test"),
                                 payload.get("mime_type", ""))
            images.append(image)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite")
            engine = GatewayEngine(store, MockTransport())
            await engine.start()
            session = store.create_session(conversation_id=CONVERSATION_ID)
            req, _ = store.enqueue(
                session_id=session["session_id"],
                client_request_id="download-existing-image",
                prompt="media download only",
            )
            ids = [
                store.save_image(req["request_id"], session["session_id"], item, role="assistant")
                for item in images
            ]
            client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
            await client.start_server()
            try:
                response = await client.get("/v1/media/" + ids[0])
                output = await response.read()
                result = {
                    "found_refs": len(refs),
                    "retrieved_images": len(images),
                    "content_type": response.content_type,
                    "http_status": response.status,
                    "bytes": len(output),
                    "binary_matches": output == images[0].data,
                    "png_signature": output.startswith(b"\x89PNG\r\n\x1a\n"),
                }
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if response.status != 200 or not result["binary_matches"]:
                    raise RuntimeError("HTTP binary image verification failed")
            finally:
                await client.close()
                await engine.stop()
                store.close()
    finally:
        await page.close()
        await transport.close()


if __name__ == "__main__":
    asyncio.run(main())
