"""Opt-in live browser image recognition smoke test.

Sends a synthetic red triangle and blue rectangle through authenticated Chrome.
Only checks completion; doesn't modify the production gateway.
"""
import asyncio
import os
import json
from pathlib import Path

from chatbridge.browser_transport import BrowserTransport
from chatbridge.media import verify_image


async def main():
    png = Path("/tmp/chatbridge_multimodal_probe.png").read_bytes()
    image = verify_image(png, "shapes.png")
    transport = BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"), hard_timeout_seconds=135)
    try:
        events = []
        answer = ""
        async for ev in transport.generate(
            session_id="live-image-smoke",
            conversation_id=None,
            prompt="На приложенном тестовом рисунке какие две фигуры и каких они цветов? Одно короткое предложение.",
            request_id="live-image-smoke",
            images=[image],
        ):
            events.append(ev.type)
            if ev.type == "text_delta":
                answer += ev.content
            if ev.type == "text_replace":
                answer = ev.content
            if ev.type == "completed":
                print(json.dumps({
                    "completed": True,
                    "conversation_id": ev.conversation_id,
                    "image_uploaded": True,
                    "answer": answer[:300],
                    "events": events,
                }, ensure_ascii=False), flush=True)
                break
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:250], "events": events},
                         ensure_ascii=False), flush=True)
        raise
    finally:
        await transport.close()


if __name__ == "__main__":
    asyncio.run(main())
