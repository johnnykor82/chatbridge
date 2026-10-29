"""Real end-to-end attachment test using a synthetic spoken WAV recording."""
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
    wav=Path("/tmp/chatbridge_spoken_test.wav").read_bytes()
    with tempfile.TemporaryDirectory(prefix="chatbridge-audio-http-") as tmp:
        store=Store(Path(tmp)/"state.db")
        transport=BrowserTransport(os.getenv("CHATBRIDGE_CDP", "http://127.0.0.1:9437"),hard_timeout_seconds=180)
        engine=GatewayEngine(store,transport)
        await engine.start()
        client=TestClient(TestServer(make_app(store,engine,Settings(transport="browser"))))
        await client.start_server()
        try:
            sid=(await (await client.post("/v1/sessions",json={"title":"audio-file-live"})).json())["session_id"]
            r=await client.post(f"/v1/sessions/{sid}/messages",json={
                "content":"Прослушай аудиозапись и назови секретное слово и дату встречи, если можешь. Не придумывай.",
                "client_request_id":"spoken-wav-live",
                "attachments":[{
                    "filename":"meeting-voice.wav","mime_type":"audio/wav",
                    "data_base64":base64.b64encode(wav).decode(),
                }],
            })
            submitted=await r.json()
            rid=submitted["request_id"]
            print("SEND",r.status,"id",rid,flush=True)
            try:await engine.wait_terminal(rid,205)
            except asyncio.TimeoutError:pass
            row=(await (await client.get("/v1/requests/"+rid)).json())
            files=row["attachments"]
            print("FINAL",json.dumps({
                "status":row["status"],"answer":row["answer"][:650],
                "file_count":len(files),"file_types":[f["mime_type"] for f in files],
                "error":row["last_error"],"conversation_id":row["conversation_id"],
                "recognition":("maple" in row["answer"].lower() and "tuesday" in row["answer"].lower()),
            },ensure_ascii=False),flush=True)
        finally:
            await client.close()
            await engine.stop()
            store.close()

if __name__=="__main__":
    asyncio.run(main())
