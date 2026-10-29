"""Smoke test the *running* ChatBridge service after a multimodal rollout.

Creates two independent real ChatGPT conversations, sends mixed file inputs,
downloads all generated attachments and validates binary integrity.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path

import aiohttp

BASE = os.getenv("CHATBRIDGE_URL", "http://127.0.0.1:8788")
END = {"completed", "failed", "unknown", "interrupted"}
STAMP = str(int(time.time()))


async def session(client: aiohttp.ClientSession, title: str) -> str:
    async with client.post(BASE + "/v1/sessions", json={"title": title}) as response:
        data = await response.json()
        assert response.status == 201, (response.status, data)
        return data["session_id"]


async def run_task(client, title, body, *, expected_input=None):
    sid = await session(client, title)
    async with client.post(f"{BASE}/v1/sessions/{sid}/messages", json=body) as response:
        info = await response.json()
        assert response.status == 202, (response.status, info)
    rid = info["request_id"]
    print("SUBMITTED", title, sid, rid, flush=True)
    if expected_input:
        incoming = info["attachments"]
        if len(incoming) != len(expected_input):
            raise AssertionError(f"Stored only {len(incoming)} of {len(expected_input)} files")
        for meta, raw in zip(incoming, expected_input, strict=True):
            async with client.get(BASE + "/v1/media/" + meta["media_id"]) as response:
                retrieved = await response.read()
                assert response.status == 200 and retrieved == raw, meta
    for _ in range(155):
        async with client.get(BASE + "/v1/requests/" + rid) as response:
            result = await response.json()
            assert response.status == 200
        if result["status"] in END:
            break
        await asyncio.sleep(1)
    else:
        raise AssertionError("Request did not reach a terminal state in test deadline")
    saved = [x for x in result["attachments"] if x["role"] == "assistant"]
    downloads = []
    for meta in saved:
        async with client.get(BASE + "/v1/media/" + meta["media_id"]) as response:
            data = await response.read()
            downloads.append({
                "filename": meta["filename"],
                "mime_type": meta["mime_type"],
                "length": len(data),
                "http": response.status,
                "hash_verified": hashlib.sha256(data).hexdigest() == meta["sha256"],
            })
    report = {
        "title": title,
        "request_id": rid,
        "session_id": sid,
        "conversation_id": result["conversation_id"],
        "state": result["status"],
        "answer": result["answer"][:330],
        "uploads": len(expected_input or []),
        "downloads": downloads,
        "attachments_complete": result.get("attachments_complete"),
        "attachment_errors": result.get("attachment_errors"),
        "last_error": result.get("last_error"),
    }
    print("RESULT", json.dumps(report, ensure_ascii=False), flush=True)
    return report


async def main():
    png = Path("/tmp/chatbridge_multimodal_probe.png").read_bytes()
    wav = Path("/tmp/chatbridge_spoken_test.wav").read_bytes()
    note = b"RELEASE-VERIFIED-251026\n"
    upload = {
        "content": "Рассмотри картинку и назови две фигуры и их цвета. "
                   "Прочитай текстовый файл и повтори его маркер. "
                   "Аудиофайл также приложен, сообщи, что удалось услышать.",
        "client_request_id": "multimodal-rollout-in-" + STAMP,
        "attachments": [
            {"filename": "shapes.png", "mime_type": "image/png",
             "data_base64": base64.b64encode(png).decode()},
            {"filename": "memo.wav", "mime_type": "audio/wav",
             "data_base64": base64.b64encode(wav).decode()},
            {"filename": "mark.txt", "mime_type": "text/plain",
             "data_base64": base64.b64encode(note).decode()},
        ],
    }
    output = {
        "content": "Создай два РАЗНЫХ файла для скачивания и предоставь две карточки вложений: "
                   "bridgecheck.csv с двумя строками id,value и 1,42; "
                   "bridgecheck.py с единственной строкой print(42). "
                   "Файлы должны быть настоящими, а не блоками кода.",
        "client_request_id": "multimodal-rollout-out-" + STAMP,
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=190)) as client:
        reports = await asyncio.gather(
            run_task(client, "rollout-mixed-input", upload, expected_input=[png, wav, note]),
            run_task(client, "rollout-generated-files", output),
        )
    mixed, generated = reports
    passed = (
        mixed["state"] == "completed"
        and generated["state"] == "completed"
        and len(generated["downloads"]) >= 2
        and all(x["http"] == 200 and x["hash_verified"]
                for x in generated["downloads"])
        and generated["attachments_complete"] is True
    )
    print("FINAL", json.dumps({"passed": passed, "reports": len(reports)},
                              ensure_ascii=False), flush=True)
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
