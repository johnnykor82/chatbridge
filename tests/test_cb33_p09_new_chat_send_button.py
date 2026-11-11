"""P09 native send preference for a NEW chat, never two gestures."""
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from chatbridge.browser_transport import BrowserTransport,SubmissionUncertainError


def fake_page(button_count=1,enabled=True,click_fails=False):
    editor=Mock()
    editor.first=editor
    editor.wait_for=AsyncMock()
    editor.press=AsyncMock()
    button=Mock()
    button.first=button
    button.count=AsyncMock(return_value=button_count)
    button.is_enabled=AsyncMock(return_value=enabled)
    button.click=AsyncMock(side_effect=RuntimeError("uncertain click") if click_fails else None)
    page=Mock()
    page.locator.return_value=editor
    page.get_by_role.return_value=button
    page.add_init_script=AsyncMock()
    page.goto=AsyncMock()
    page.evaluate=AsyncMock(side_effect=[
        {"assistantCount":0,"uiErrorCount":0},
        {"requestId":"source-id","httpStatus":200,"uiErrorCount":0}
    ])
    page.close=AsyncMock()
    page.is_closed.return_value=False
    transport=BrowserTransport("ws://unused",hard_timeout_seconds=10)
    transport._ensure=AsyncMock(return_value=SimpleNamespace(contexts=[object()]))
    transport._new_page=AsyncMock(return_value=page)
    transport._configure_reasoning_effort=AsyncMock()
    transport._fill_composer=AsyncMock()
    return transport,button,editor


@pytest.mark.asyncio
async def test_new_chat_prefers_enabled_native_send_button_once():
    transport,button,editor=fake_page()
    try:
        async with aclosing(transport.generate(
            session_id="s",conversation_id=None,prompt="original",request_id="rid"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            assert (await anext(stream)).type=="submitted"
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("count,enabled",[(0,True),(1,False)])
async def test_new_chat_enter_fallback_only_without_usable_send(count,enabled):
    transport,button,editor=fake_page(button_count=count,enabled=enabled)
    try:
        async with aclosing(transport.generate(
            session_id="s",conversation_id=None,prompt="original",request_id="rid"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            assert (await anext(stream)).type=="submitted"
        button.click.assert_not_awaited()
        editor.press.assert_awaited_once_with("Enter")
    finally:await transport.close()


@pytest.mark.asyncio
async def test_new_chat_button_exception_never_triggers_enter_retry():
    transport,button,editor=fake_page(click_fails=True)
    try:
        async with aclosing(transport.generate(
            session_id="s",conversation_id=None,prompt="original",request_id="rid"
        )) as stream:
            assert (await anext(stream)).type=="send_attempted"
            with pytest.raises(SubmissionUncertainError):
                await anext(stream)
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:await transport.close()
