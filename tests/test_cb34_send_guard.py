"""CB34: fail-closed source receipt timeout and native composer preflight."""
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chatbridge.browser_transport import BrowserTransport, SubmissionUncertainError


def browser(*, visible_name=True, button_enabled=True, css_count=0,
            url="https://chatgpt.com/g/g-p-" + "a"*32 + "/project",
            states=None):
    editor=Mock()
    editor.first=editor
    editor.wait_for=AsyncMock()
    editor.press=AsyncMock()
    button=Mock()
    button.first=button
    button.count=AsyncMock(return_value=1 if visible_name else 0)
    button.is_visible=AsyncMock(return_value=True)
    button.is_enabled=AsyncMock(return_value=button_enabled)
    button.click=AsyncMock()
    css=Mock()
    css.first=css
    css.count=AsyncMock(return_value=css_count)
    css.is_visible=AsyncMock(return_value=True)
    css.is_enabled=AsyncMock(return_value=button_enabled)
    css.click=AsyncMock()
    page=Mock()
    page.url=url
    page.get_by_role.return_value=button
    page.locator.side_effect=lambda q:editor if "contenteditable" in q else css
    page.add_init_script=AsyncMock()
    page.goto=AsyncMock()
    page.close=AsyncMock()
    page.is_closed.return_value=False
    baseline={"assistantCount":0,"uiErrorCount":0}
    empty={"requestId":None,"httpStatus":None,"uiErrorCount":0,
           "assistantCount":0,"isGenerating":False,"url":url}
    page.evaluate=AsyncMock(side_effect=[baseline]+(states or [empty]*16))
    bt=BrowserTransport("ws://not-used",hard_timeout_seconds=15,
                        send_receipt_timeout_seconds=0.01,
                        send_preflight_timeout_seconds=0.01)
    bt._ensure=AsyncMock(return_value=SimpleNamespace(contexts=[object()]))
    bt._new_page=AsyncMock(return_value=page)
    bt._configure_reasoning_effort=AsyncMock()
    bt._fill_composer=AsyncMock()
    bt._approve_if_needed=AsyncMock(return_value=None)
    return bt,page,button,css,editor


@pytest.mark.asyncio
async def test_missing_original_post_exits_without_30_minute_wait_or_duplicate():
    t,p,button,css,editor=browser()
    try:
        async with aclosing(t.generate(
            session_id="s",conversation_id=None,prompt="source",request_id="r"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            with pytest.raises(SubmissionUncertainError,match="receipt unconfirmed"):
                await anext(stream)
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
        css.click.assert_not_awaited()
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_expired_login_fails_before_send_attempt_or_click():
    t,p,button,css,editor=browser(url="https://chatgpt.com/auth/login?next=%2Fg")
    try:
        async with aclosing(t.generate(
            session_id="s",conversation_id=None,prompt="source",request_id="r"
        )) as stream:
            with pytest.raises(RuntimeError,match="authentication required"):
                await anext(stream)
        button.click.assert_not_awaited()
        editor.press.assert_not_awaited()
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_disabled_native_send_fails_before_journal():
    t,p,button,css,editor=browser(button_enabled=False)
    try:
        async with aclosing(t.generate(
            session_id="s",conversation_id=None,prompt="source",request_id="r"
        )) as stream:
            with pytest.raises(RuntimeError,match="native Send control"):
                await anext(stream)
        button.click.assert_not_awaited()
        editor.press.assert_not_awaited()
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_icon_only_send_does_one_click_no_enter():
    ok={"requestId":"id-1","httpStatus":200,"uiErrorCount":0,
        "assistantCount":0,"isGenerating":True,"url":"https://chatgpt.com/"}
    t,p,button,css,editor=browser(visible_name=False,css_count=1,states=[ok])
    try:
        async with aclosing(t.generate(
            session_id="s",conversation_id=None,prompt="source",request_id="r"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            event=await anext(stream)
            assert event.type=="submitted"
            assert event.message_id=="id-1"
        css.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_dom_probe_errors_after_one_click_obey_receipt_deadline():
    t,p,button,css,editor=browser()
    p.evaluate=AsyncMock(side_effect=[
        {"assistantCount":0,"uiErrorCount":0},
        *[RuntimeError("CDP DOM temporarily unreachable") for _ in range(8)],
    ])
    try:
        async with aclosing(t.generate(
            session_id="s",conversation_id=None,prompt="source",request_id="r"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            with pytest.raises(SubmissionUncertainError,match="browser DOM errors"):
                await anext(stream)
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:
        await t.close()


def test_send_receipt_timeout_setting_rejects_invalid_bounds(monkeypatch):
    from chatbridge.config import Settings
    for v in ("0","121","-1"):
        monkeypatch.setenv("CHATBRIDGE_SEND_RECEIPT_TIMEOUT",v)
        with pytest.raises(ValueError,match="SEND_RECEIPT_TIMEOUT"):
            Settings.from_env().validate()
    monkeypatch.setenv("CHATBRIDGE_SEND_RECEIPT_TIMEOUT","60")
    assert Settings.from_env().send_receipt_timeout_seconds==60
