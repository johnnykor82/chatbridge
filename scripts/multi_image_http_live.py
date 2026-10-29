"""Live stress for two independently downloadable generated image assets."""
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
    with tempfile.TemporaryDirectory(prefix="chatbridge-multi-image-") as tmp:
        store=Store(Path(tmp)/"state.db")
        transport=BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"),hard_timeout_seconds=245)
        engine=GatewayEngine(store,transport)
        await engine.start()
        client=TestClient(TestServer(make_app(store,engine,Settings(transport="browser"))))
        await client.start_server()
        try:
            sid=(await (await client.post("/v1/sessions",json={"title":"two-images-live"})).json())["session_id"]
            body={"content":"Сгенерируй ДВЕ ОТДЕЛЬНЫЕ картинки двумя файлами, не коллаж: "
                            "первая — голубой квадрат на белом фоне; "
                            "вторая — зелёный треугольник на белом фоне. "
                            "Верни обе как изображения.",
                  "client_request_id":"two-independent-pictures-live"}
            response=await client.post(f"/v1/sessions/{sid}/messages",json=body)
            row=await response.json()
            print("SENT",row["request_id"],flush=True)
            try:await engine.wait_terminal(row["request_id"],255)
            except asyncio.TimeoutError:pass
            info=(await (await client.get(f"/v1/requests/{row['request_id']}")).json())
            images=[x for x in info["attachments"] if x["role"]=="assistant" and x["mime_type"].startswith("image/")]
            checks=[]
            for item in images:
                d=await client.get("/v1/media/"+item["media_id"])
                b=await d.read()
                checks.append({"ok":d.status==200 and len(b)>1000,
                               "size":len(b),"sha256":item["sha256"][:12]})
            print("RESULT",json.dumps({"state":info["status"],"image_count":len(images),
                "refs":len(info["image_refs"]),"images":checks,
                "error":info["last_error"],"text":info["answer"][:120],
                "conversation_id":info["conversation_id"]},ensure_ascii=False),flush=True)
        finally:
            await client.close()
            await engine.stop()
            store.close()
asyncio.run(main())
