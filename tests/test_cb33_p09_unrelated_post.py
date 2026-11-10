"""P09 incident: unrelated opaque POST must never poison the CDP event listener."""
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error as PlaywrightError

from chatbridge.browser_transport import BrowserTransport, SubmissionUncertainError


class OpaquePost:
    method = "POST"

    def __init__(self, url):
        self.url = url
        self.payload_reads = 0

    @property
    def post_data_json(self):
        self.payload_reads += 1
        raise PlaywrightError("POST data is not a valid JSON object")

    @property
    def post_data(self):
        self.payload_reads += 1
        return "not-json"


def fake_browser():
    page = Mock()
    editor = Mock()
    editor.first = editor
    editor.wait_for = AsyncMock()
    editor.press = AsyncMock()
    page.locator.return_value = editor
    page.is_closed.return_value = False
    page.add_init_script = AsyncMock()
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.evaluate = AsyncMock(return_value={"assistantCount":0,"uiErrorCount":0})
    transport = BrowserTransport("ws://not-used", hard_timeout_seconds=0.35)
    transport._ensure = AsyncMock(return_value=SimpleNamespace(contexts=[object()]))
    transport._new_page = AsyncMock(return_value=page)
    transport._configure_reasoning_effort = AsyncMock()
    transport._fill_composer = AsyncMock()
    handlers = {}
    page.on.side_effect = lambda event,callback: handlers.setdefault(event,[]).append(callback)
    return transport, page, editor, handlers


@pytest.mark.asyncio
async def test_unrelated_opaque_post_does_not_parse_during_navigation():
    transport,page,editor,handlers = fake_browser()
    opaque = OpaquePost("https://chatgpt.com/backend-api/telemetry/binary")
    async def navigate(*args,**kwargs):
        for callback in handlers["request"]:
            callback(opaque)
    page.goto.side_effect = navigate
    try:
        async with aclosing(transport.generate(
            session_id="isolated",conversation_id=None,prompt="exact source",
            request_id="new-test")) as stream:
            assert (await anext(stream)).type == "send_attempted"
            assert opaque.payload_reads == 0
            editor.press.assert_not_awaited()
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_malformed_source_post_fails_closed_without_listener_exception():
    transport,page,editor,handlers = fake_browser()
    opaque = OpaquePost("https://chatgpt.com/backend-api/f/conversation")
    async def click_once(*args):
        for callback in handlers["request"]:
            callback(opaque)
    editor.press.side_effect = click_once
    try:
        async with aclosing(transport.generate(
            session_id="isolated",conversation_id=None,prompt="exact source",
            request_id="new-test")) as stream:
            assert (await anext(stream)).type == "send_attempted"
            with pytest.raises((SubmissionUncertainError,TimeoutError)):
                await anext(stream)
            editor.press.assert_awaited_once_with("Enter")
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_exact_json_source_post_preserves_http_ack():
    transport,page,editor,handlers = fake_browser()
    page.evaluate = AsyncMock(side_effect=[
        {"assistantCount":0,"uiErrorCount":0},
        {"requestId":"id-original","httpStatus":200,"uiErrorCount":0},
    ])
    valid = SimpleNamespace(
        method="POST",url="https://chatgpt.com/backend-api/f/conversation",
        post_data_json={"messages":[{"id":"id-original","author":{"role":"user"},
              "content":{"parts":["exact source"]}}],"parent_message_id":"old-parent"},
    )
    async def click_once(*args):
        for callback in handlers["request"]:
            callback(valid)
        reply=SimpleNamespace(request=valid,status=200,
          url="https://chatgpt.com/backend-api/f/conversation",headers={})
        for callback in handlers["response"]:
            callback(reply)
    editor.press.side_effect=click_once
    try:
        async with aclosing(transport.generate(
            session_id="isolated",conversation_id=None,prompt="exact source",
            request_id="new-test")) as stream:
            assert (await anext(stream)).type == "send_attempted"
            ack=await anext(stream)
            assert ack.type=="submitted" and ack.message_id=="id-original"
            assert ack.meta["http_status"]==200
            editor.press.assert_awaited_once_with("Enter")
    finally:
        await transport.close()
