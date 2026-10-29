"""Multipart upload accepts generic binary files independent of image whitelist."""

import tempfile
from pathlib import Path

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store


@pytest.mark.asyncio
async def test_multipart_file_only_with_mixed_extensions():
    with tempfile.TemporaryDirectory() as dirname:
        store = Store(Path(dirname) / "db")
        engine = GatewayEngine(store, MockTransport(delay=0.01))
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        try:
            sid = (await (await client.post("/v1/sessions", json={})).json())["session_id"]
            form = FormData()
            form.add_field(
                "attachments",
                b"PK\x03\x04docx",
                filename="report.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            form.add_field(
                "attachments",
                b"#!/bin/sh\necho ok\n",
                filename="health.sh",
                content_type="text/x-shellscript",
            )
            form.add_field(
                "attachments",
                b"PK\x03\x04zip",
                filename="archive.zip",
                content_type="application/zip",
            )
            response = await client.post(f"/v1/sessions/{sid}/messages", data=form)
            assert response.status == 202
            request = await response.json()
            await engine.wait_terminal(request["request_id"], 5)
            assets = store.list_media(request["request_id"], "user")
            assert sorted(a["filename"] for a in assets) == [
                "archive.zip",
                "health.sh",
                "report.docx",
            ]
            for asset in assets:
                result = await client.get("/v1/media/" + asset["media_id"])
                assert result.status == 200
                assert result.headers["Content-Disposition"].startswith("attachment;")
                assert len(await result.read()) > 0
        finally:
            await client.close()
            await engine.stop()
            store.close()
