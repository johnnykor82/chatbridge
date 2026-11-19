"""P09 native send preference for a NEW chat, never two gestures."""

from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chatbridge.browser_transport import BrowserTransport, SubmissionUncertainError


def fake_page(button_count=1, enabled=True, click_fails=False):
    editor = Mock()
    editor.first = editor
    editor.wait_for = AsyncMock()
    editor.press = AsyncMock()
    button = Mock()
    button.first = button
    button.count = AsyncMock(return_value=button_count)
    button.is_enabled = AsyncMock(return_value=enabled)
    button.is_visible = AsyncMock(return_value=True)
    button.click = AsyncMock(side_effect=RuntimeError("uncertain click") if click_fails else None)
    css_button = Mock()
    css_button.first = css_button
    css_button.count = AsyncMock(return_value=0)
    css_button.is_enabled = AsyncMock(return_value=True)
    css_button.is_visible = AsyncMock(return_value=True)
    css_button.click = AsyncMock()
    page = Mock()
    page.locator.side_effect = lambda selector: (
        editor if "contenteditable" in selector else css_button)
    page.css_button = css_button
    page.url = "https://chatgpt.com/g/g-p-" + "a"*32 + "/project"
    page.get_by_role.return_value = button
    page.add_init_script = AsyncMock()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock(
        side_effect=[
            {"assistantCount": 0, "uiErrorCount": 0},
            {"requestId": "source-id", "httpStatus": 200, "uiErrorCount": 0},
        ]
    )
    page.close = AsyncMock()
    page.is_closed.return_value = False
    transport = BrowserTransport("ws://unused", hard_timeout_seconds=10, send_preflight_timeout_seconds=0.01)
    transport._ensure = AsyncMock(return_value=SimpleNamespace(contexts=[object()]))
    transport._new_page = AsyncMock(return_value=page)
    transport._configure_reasoning_effort = AsyncMock()
    transport._fill_composer = AsyncMock()
    return transport, button, editor


@pytest.mark.asyncio
async def test_new_chat_prefers_enabled_native_send_button_once():
    transport, button, editor = fake_page()
    try:
        async with aclosing(
            transport.generate(
                session_id="s", conversation_id=None, prompt="original", request_id="rid"
            )
        ) as stream:
            assert (await anext(stream)).type == "send_attempted"
            assert (await anext(stream)).type == "submitted"
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("count,enabled", [(0, True), (1, False)])
async def test_new_chat_missing_or_disabled_native_send_is_safe_not_sent(count, enabled):
    transport, button, editor = fake_page(button_count=count, enabled=enabled)
    try:
        async with aclosing(
            transport.generate(
                session_id="s", conversation_id=None, prompt="original", request_id="rid"
            )
        ) as stream:
            with pytest.raises(RuntimeError, match="native Send control unavailable"):
                await anext(stream)
        button.click.assert_not_awaited()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_icon_only_native_send_selected_when_role_name_is_missing():
    transport, button, editor = fake_page(button_count=0)
    # ChatGPT's icon-only send button has data-testid, but no English/Russian label.
    page = transport._new_page.return_value
    page.css_button.count = AsyncMock(return_value=1)
    try:
        async with aclosing(transport.generate(
            session_id="s",conversation_id=None,prompt="original",request_id="rid"
        )) as stream:
            assert (await anext(stream)).type == "send_attempted"
            assert (await anext(stream)).type == "submitted"
        page.css_button.click.assert_awaited_once()
        button.click.assert_not_awaited()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_new_chat_button_exception_never_triggers_enter_retry():
    transport, button, editor = fake_page(click_fails=True)
    try:
        async with aclosing(
            transport.generate(
                session_id="s", conversation_id=None, prompt="original", request_id="rid"
            )
        ) as stream:
            assert (await anext(stream)).type == "send_attempted"
            with pytest.raises(SubmissionUncertainError):
                await anext(stream)
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()
