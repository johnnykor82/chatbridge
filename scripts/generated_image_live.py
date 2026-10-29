"""Opt-in test: generated ChatGPT image must be returned as binary media.

Independent of production HTTP server. Stops after 240 seconds.
"""
import asyncio
import os
import base64
import json
import tempfile
from pathlib import Path

from chatbridge.browser_transport import BrowserTransport
from chatbridge.engine import GatewayEngine
from chatbridge.store import Store


async def main():
    with tempfile.TemporaryDirectory(prefix="bridge-outimage-") as tmp:
        store = Store(Path(tmp) / "results.db")
        transport = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"), hard_timeout_seconds=220)
        engine = GatewayEngine(store, transport)
        await engine.start()
        session = store.create_session(title="bridge-image-generation-check")
        item, _ = engine.enqueue(
            session["session_id"],
            "Сгенерируй изображение: один зелёный круг на белом фоне. "
            "Нужно именно изображение, а не SVG, ASCII или текстовое описание.",
            "generate-image-20261025",
        )
        try:
            await engine.wait_terminal(item["request_id"], timeout=235)
        except asyncio.TimeoutError:
            pass
        final = store.get_request(item["request_id"])
        refs = store.list_image_refs(item["request_id"])
        media = store.list_media(item["request_id"], role="assistant")
        print(json.dumps({
            "status": final["status"], "response": final["answer"][:220],
            "conversation_id": store.get_session(session["session_id"])["conversation_id"],
            "image_ref_count": len(refs),
            "downloaded_images": len(media),
            "image_types": [m["mime_type"] for m in media],
            "error": final["last_error"],
            "event_types": [e["kind"] for e in store.get_events(item["request_id"])],
        }, ensure_ascii=False), flush=True)
        await engine.stop()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
