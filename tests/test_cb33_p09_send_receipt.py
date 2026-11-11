"""CB33 P09: real send evidence, no premature ID fail, never resend uncertain."""
import asyncio
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chatbridge.browser_transport import BrowserTransport, DOM_JS, SubmissionUncertainError
from chatbridge.engine import GatewayEngine
from chatbridge.model import TransportEvent
from chatbridge.rate_limit import ProviderRateLimitError
from chatbridge.store import Store


def fake_browser(states):
    """Local fake page; no Chrome or network calls."""
    page = Mock()
    editor = Mock()
    editor.first = editor
    editor.wait_for = AsyncMock()
    editor.press = AsyncMock()
    page.locator.return_value = editor
    # This fixture specifically tests Enter fallback without a native Send
    # button. Native enabled button behavior is covered by P06B regressions.
    send_button = Mock()
    send_button.first = send_button
    send_button.count = AsyncMock(return_value=0)
    send_button.is_enabled = AsyncMock(return_value=False)
    send_button.click = AsyncMock()
    page.get_by_role.return_value = send_button
    page.is_closed.return_value = False
    page.add_init_script = AsyncMock()
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.evaluate = AsyncMock(side_effect=states)
    transport = BrowserTransport("ws://never-used",hard_timeout_seconds=15)
    transport._ensure = AsyncMock(return_value=SimpleNamespace(contexts=[object()]))
    transport._new_page = AsyncMock(return_value=page)
    transport._configure_reasoning_effort = AsyncMock()
    transport._fill_composer = AsyncMock()
    return transport,page,editor


@pytest.mark.asyncio
async def test_delayed_http200_and_user_id_after_send_are_not_prematurely_unknown():
    transport,page,editor=fake_browser([
        {"assistantCount":0, "uiErrorCount":0},
        {"requestId":None,"httpStatus":None,"uiErrorCount":0},
        {"requestId":"source-user-1","httpStatus":200,"uiErrorCount":0}
    ])
    try:
        async with aclosing(transport.generate(
                session_id="session-1",conversation_id=None,prompt="source body",
                request_id="rid-1")) as stream:
            intent=await anext(stream)
            assert intent.type=="send_attempted"
            observed=await anext(stream)
            assert observed.type=="submitted"
            assert observed.message_id=="source-user-1"
            editor.press.assert_awaited_once_with("Enter")
    finally:await transport.close()


@pytest.mark.asyncio
async def test_generated_user_id_without_http_success_is_not_receipt():
    transport,page,editor=fake_browser([
        {"assistantCount":0,"uiErrorCount":0},
        {"requestId":"fake-local-id","httpStatus":None,"uiErrorCount":0},
        {"requestId":"fake-local-id","httpStatus":429,"uiErrorCount":0},
    ])
    try:
        async with aclosing(transport.generate(
                session_id="s",conversation_id=None,prompt="source",request_id="r")) as stream:
            intent=await anext(stream)
            assert intent.type=="send_attempted"
            with pytest.raises(SubmissionUncertainError):
                await anext(stream)
            editor.press.assert_awaited_once_with("Enter")
    finally:await transport.close()


class AttemptedThenThrottled:
    def __init__(self):
        self.calls=0
    async def generate(self,**kwargs):
        self.calls+=1
        yield TransportEvent("send_attempted")
        raise ProviderRateLimitError("Network throttled after original Send")
    async def close(self):
        pass


@pytest.mark.asyncio
async def test_engine_journals_attempt_and_never_requeues_after_429(tmp_path):
    store=Store(tmp_path/"state.sqlite")
    transport=AttemptedThenThrottled()
    engine=GatewayEngine(store,transport)
    try:
        sid=store.create_session()["session_id"]
        request,_=store.enqueue(session_id=sid,prompt="once",client_request_id="stable")
        rid=request["request_id"]
        await engine._run_once(rid,sid)
        assert store.get_request(rid)["status"]=="unknown"
        assert transport.calls==1
        events=store.get_events(rid)
        assert [e["kind"] for e in events].count("send_attempted")==1
        assert all(e["kind"]!="completed" for e in events)
    finally:store.close()


def test_no_arbitrary_dom_assistant_character_truncation():
    assert "content.slice(0,500000)" not in DOM_JS


@pytest.mark.asyncio
async def test_page_network_source_id_and_http200_rescue_missing_dom_hook():
    transport,page,editor=fake_browser([
        {"assistantCount":0,"uiErrorCount":0},
        {"requestId":None,"httpStatus":None,"uiErrorCount":0},
    ])
    transport.hard_timeout=0.3
    handlers={}
    def on(event, callback):
        handlers.setdefault(event,[]).append(callback)
    page.on.side_effect=on
    async def send_one(*args):
        req=SimpleNamespace(method="POST",
            url="https://chatgpt.com/backend-api/f/conversation",
            post_data_json={"messages":[{"id":"network-user-1",
                "author":{"role":"user"},
                "content":{"parts":["source body"]}}],
                "parent_message_id":"parent-1"})
        for callback in handlers.get("request",[]):callback(req)
        response=SimpleNamespace(request=req,status=200,url=req.url,headers={})
        for callback in handlers.get("response",[]):callback(response)
    editor.press.side_effect=send_one
    try:
        async with aclosing(transport.generate(
                session_id="s",conversation_id=None,prompt="source body",
                request_id="rid")) as stream:
            assert (await anext(stream)).type=="send_attempted"
            ack=await anext(stream)
            assert ack.type=="submitted"
            assert ack.message_id=="network-user-1"
            assert ack.parent_message_id=="parent-1"
            editor.press.assert_awaited_once()
    finally:await transport.close()


@pytest.mark.asyncio
async def test_unrelated_network_post_never_fakes_source_receipt():
    transport,page,editor=fake_browser([
        {"assistantCount":0,"uiErrorCount":0},
        {"requestId":None,"httpStatus":None,"uiErrorCount":0},
    ])
    transport.hard_timeout=0.3
    handlers={}
    def on(event,callback):
        handlers.setdefault(event,[]).append(callback)
    page.on.side_effect=on
    async def send_one(*args):
        req=SimpleNamespace(method="POST",
            url="https://chatgpt.com/backend-api/f/conversation",
            post_data_json={"messages":[{"id":"foreign",
                "author":{"role":"user"},"content":{"parts":["other prompt"]}}]})
        for callback in handlers.get("request",[]):callback(req)
        response=SimpleNamespace(request=req,status=200,url=req.url,headers={})
        for callback in handlers.get("response",[]):callback(response)
    editor.press.side_effect=send_one
    try:
        async with aclosing(transport.generate(
                session_id="s",conversation_id=None,prompt="source body",
                request_id="rid")) as stream:
            assert (await anext(stream)).type=="send_attempted"
            with pytest.raises((TimeoutError,SubmissionUncertainError)):
                await anext(stream)
            editor.press.assert_awaited_once()
    finally:await transport.close()


@pytest.mark.asyncio
async def test_conflicting_browser_and_network_message_ids_fail_closed():
    transport,page,editor=fake_browser([
        {"assistantCount":0,"uiErrorCount":0},
        {"requestId":"wrong-id","httpStatus":200,"uiErrorCount":0},
    ])
    transport.hard_timeout=0.4
    handlers={}
    page.on.side_effect=lambda event,callback: handlers.setdefault(event,[]).append(callback)
    async def send_one(*args):
        req=SimpleNamespace(method="POST",
            url="https://chatgpt.com/backend-api/f/conversation",
            post_data_json={"messages":[{"id":"correct-id",
                "author":{"role":"user"},
                "content":{"parts":["source text"]}}]})
        for callback in handlers.get("request",[]):callback(req)
        response=SimpleNamespace(request=req,status=200,url=req.url,headers={})
        for callback in handlers.get("response",[]):callback(response)
    editor.press.side_effect=send_one
    try:
        async with aclosing(transport.generate(
            session_id="s",conversation_id=None,prompt="source text",request_id="rid"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            with pytest.raises(SubmissionUncertainError):
                await anext(stream)
            editor.press.assert_awaited_once()
    finally:await transport.close()


def test_original_turn_source_match_does_not_truncate_130000_chars():
    from chatbridge.browser_transport import _original_turn_post
    prompt="full-text-fragment-"*7222
    prompt=prompt[:130000]
    assert len(prompt)==130000
    body={"messages":[{"id":"full-text-user-1","author":{"role":"user"},
                       "content":{"parts":[prompt]}}]}
    original=_original_turn_post(
        "POST","https://chatgpt.com/backend-api/f/conversation",body,prompt
    )
    assert original["user_id"]=="full-text-user-1"
    assert _original_turn_post("POST","https://chatgpt.com/backend-api/f/conversation",
                              body,prompt+"changed") is None


def test_http_accepted_source_user_id_is_atomic_and_idempotent(tmp_path):
    store=Store(tmp_path/"state.sqlite")
    try:
        sid=store.create_session(conversation_id="original-conversation")["session_id"]
        req,_=store.enqueue(session_id=sid,prompt="source",client_request_id="key")
        rid=req["request_id"]
        store.set_status(rid,"submitting")
        assert store.record_http_accepted(
            rid,sid,"browser-user-1",parent_message_id="parent-1",
            http_status=200,observation_source="page_network")
        assert not store.record_http_accepted(
            rid,sid,"browser-user-1",parent_message_id="parent-1",
            http_status=200,observation_source="page_network")
        state=store.get_request(rid)
        assert state["user_message_id"]=="browser-user-1"
        assert state["parent_message_id"]=="parent-1"
        ev=[e for e in store.get_events(rid) if e["kind"]=="http_accepted"]
        assert len(ev)==1 and ev[0]["payload"]["http_status"]==200
        assert not store.record_http_accepted(
            rid,sid,"different-user",parent_message_id="parent-1",
            http_status=200,observation_source="page_network")
    finally:store.close()


class AttemptedAndAcceptedTransport:
    def __init__(self):self.count=0
    async def generate(self,**kwargs):
        self.count+=1
        yield TransportEvent("send_attempted")
        yield TransportEvent("submitted",conversation_id=kwargs["conversation_id"],
            message_id="browser-user-1",parent_message_id="parent-1",
            meta={"http_status":200,"observation_source":"page_network"})
    async def close(self):pass


@pytest.mark.asyncio
async def test_engine_persists_distinct_intent_and_provider_http_acceptance(tmp_path):
    store=Store(tmp_path/"state.sqlite")
    transport=AttemptedAndAcceptedTransport()
    engine=GatewayEngine(store,transport)
    try:
        cid="11111111-2222-4333-8444-555555555555"
        sid=store.create_session(conversation_id=cid)["session_id"]
        did="a"*32
        original="[MC-WATCH:"+did+"]\nExactly one notification."
        req,_=store.enqueue(session_id=sid,prompt=original,
                            client_request_id="watcher-delivery-"+did)
        await engine._run_once(req["request_id"],sid)
        assert store.get_request(req["request_id"])["status"]=="completed"
        events=[e["kind"] for e in store.get_events(req["request_id"])]
        assert events.index("send_attempted")<events.index("http_accepted")<events.index("completed")
        assert transport.count==1
    finally:store.close()


class ClientIdWithoutHttpProof:
    async def generate(self,**kwargs):
        yield TransportEvent("send_attempted")
        yield TransportEvent("submitted",conversation_id=kwargs["conversation_id"],
                             message_id="unverified-client-id")
        raise SubmissionUncertainError("No HTTP receipt ever arrived")
    async def close(self):pass


@pytest.mark.asyncio
async def test_watcher_must_not_complete_on_unverified_client_id(tmp_path):
    store=Store(tmp_path/"state.db")
    engine=GatewayEngine(store,ClientIdWithoutHttpProof())
    try:
        sid=store.create_session(
            conversation_id="11111111-2222-4333-8444-555555555555")["session_id"]
        did="c"*32
        req,_=store.enqueue(
            session_id=sid,
            prompt="[MC-WATCH:"+did+"]\nExactly this event.",
            client_request_id="watcher-delivery-"+did)
        await engine._run_once(req["request_id"],sid)
        assert store.get_request(req["request_id"])["status"]=="unknown"
        assert not any(e["kind"]=="completed" for e in store.get_events(req["request_id"]))
    finally:store.close()
