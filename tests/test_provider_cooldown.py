"""Durable provider quota coordination; no live browser required."""

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.browser_transport import SubmissionUncertainError
from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.model import TransportEvent
from chatbridge.rate_limit import ProviderRateLimitError, retry_after_seconds
from chatbridge.server import make_app
from chatbridge.store import Store


def test_durable_rate_limit_and_parser():
    assert retry_after_seconds("25") == 25
    assert retry_after_seconds("7200") == 3600
    assert retry_after_seconds("garbage") == 0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.db"
        store = Store(path)
        now = time.time()
        first = store.note_provider_rate_limit(25, now=now)
        assert first["retry_after"] >= 60
        assert store.note_provider_rate_limit(25, now=now + 0.5)["incidents"] == 1
        second = store.note_provider_rate_limit(25, now=now + 5)
        assert second["incidents"] == 2 and second["paused_until"] > first["paused_until"]
        store.close()
        reopened = Store(path)
        assert reopened.rate_limit_status()["paused_until"] == second["paused_until"]
        reopened.close()


@pytest.mark.asyncio
async def test_paused_api_and_idempotent_queue_backpressure():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "state.db")
        engine = GatewayEngine(store, MockTransport(), max_queued=1)
        engine.note_provider_rate_limit(90, "test_429")
        await engine.start()
        client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
        await client.start_server()
        try:
            health = await (await client.get("/health")).json()
            assert health["rate_limit"]["provider_rate_limited"]
            for route in ["/ready", "/v1/sessions/no/turn-state"]:
                result = await client.get(route)
                assert result.status == 503 and int(result.headers["Retry-After"]) >= 1
            session = await (await client.post("/v1/sessions", json={})).json()
            url = "/v1/sessions/" + session["session_id"] + "/messages"
            payload = {"content": "first", "client_request_id": "idem"}
            one = await client.post(url, json=payload)
            assert one.status == 202
            first = await one.json()
            assert first["rate_limit"]["provider_rate_limited"]
            again = await client.post(url, json=payload)
            assert again.status == 200
            assert (await again.json())["request_id"] == first["request_id"]
            full = await client.post(url, json={"content": "second", "client_request_id": "second"})
            assert full.status == 503 and (await full.json())["error"]["code"] == "queue_full"
            assert store.count("requests") == 1
        finally:
            await client.close()
            await engine.stop()
            store.close()


@pytest.mark.asyncio
async def test_global_pause_holds_multiple_sessions_and_drains():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "paused.db")
        transport = MockTransport(delay=0.01)
        engine = GatewayEngine(store, transport, max_active=2)
        engine.note_provider_rate_limit(60, "test")
        store.db.execute("UPDATE provider_rate_limit SET paused_until=?", (time.time() + 0.1,))
        store.db.commit()
        await engine.start()
        try:
            a = store.create_session()["session_id"]
            b = store.create_session()["session_id"]
            ra = engine.enqueue(a, "alpha", "unique-a")[0]["request_id"]
            rb = engine.enqueue(b, "beta", "unique-b")[0]["request_id"]
            await asyncio.sleep(0.03)
            assert transport.calls == []
            assert all(store.get_request(r)["status"] == "queued" for r in [ra, rb])
            await engine.wait_terminal(ra, 8)
            await engine.wait_terminal(rb, 8)
            assert len(transport.calls) == 2
            assert set(transport.calls) == {(a, "alpha"), (b, "beta")}
        finally:
            await engine.stop()
            store.close()


class ThrottledTransport(MockTransport):
    def __init__(self, post_submit):
        super().__init__()
        self.post_submit = post_submit
        self.tries = 0
        self.sent = 0
        self.engine = None

    async def generate(self, **kwargs):
        self.tries += 1
        if self.tries == 1 and not self.post_submit:
            self.engine.note_provider_rate_limit(60, "before_send")
            self.engine.store.db.execute(
                "UPDATE provider_rate_limit SET paused_until=?", (time.time() + 0.04,)
            )
            self.engine.store.db.commit()
            raise ProviderRateLimitError("Not sent")
        self.sent += 1
        yield TransportEvent("submitted", message_id="user-1", conversation_id="cid-1")
        if self.post_submit:
            self.engine.note_provider_rate_limit(60, "after_send")
            raise SubmissionUncertainError("Cannot tell if finished")
        yield TransportEvent("completed", message_id="assistant-1", conversation_id="cid-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "post_submit,expected_status,expected_tries",
    [
        (False, "completed", 2),
        (True, "unknown", 1),
    ],
)
async def test_rate_limit_never_resends_submitted_prompt(
    post_submit, expected_status, expected_tries
):
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "one.db")
        transport = ThrottledTransport(post_submit)
        engine = GatewayEngine(store, transport)
        transport.engine = engine
        await engine.start()
        try:
            sid = store.create_session()["session_id"]
            rid = engine.enqueue(sid, "check once", "stable-id")[0]["request_id"]
            await engine.wait_terminal(rid, 4)
            assert store.get_request(rid)["status"] == expected_status
            assert transport.tries == expected_tries
            assert transport.sent == 1
            assert engine.enqueue(sid, "check once", "stable-id")[1] is False
        finally:
            await engine.stop()
            store.close()


def test_browser_response_observer_only_pauses_for_chatgpt_429():
    from chatbridge.browser_transport import BrowserTransport

    class Response:
        def __init__(self, code, url, retry):
            self.status = code
            self.url = url
            self.headers = {"retry-after": retry}

    observed = []
    transport = BrowserTransport("http://127.0.0.1:1")
    transport.set_rate_limit_handler(lambda seconds, source: observed.append((seconds, source)))
    transport._observe_provider_response(Response(200, "https://chatgpt.com/", "60"))
    transport._observe_provider_response(Response(429, "https://unrelated.test/api/", "60"))
    assert observed == []
    transport._observe_provider_response(Response(429, "https://chatgpt.com/", "25"))
    transport._observe_provider_response(
        Response(429, "https://chatgpt.com/backend-api/conversation/id", "45")
    )
    assert observed == [(25.0, "provider_http_429"), (45.0, "provider_http_429")]
