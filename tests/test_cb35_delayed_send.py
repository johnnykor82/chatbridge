"""CB35: real-life delayed native Send readiness following multiple JSON attachments."""
from contextlib import aclosing
from unittest.mock import AsyncMock

import pytest
from test_cb33_p09_new_chat_send_button import fake_page

from chatbridge.browser_transport import BrowserTransport
from chatbridge.config import Settings


@pytest.mark.asyncio
async def test_disabled_then_enabled_native_send_submits_exactly_once():
    transport, button, editor = fake_page()
    transport.send_preflight_timeout_seconds = 5.0
    # ChatGPT needs several asynchronous rendering passes after 4 attachments.
    button.is_enabled = AsyncMock(side_effect=[False, False, True])
    try:
        async with aclosing(
            transport.generate(
                session_id="medical-test", conversation_id=None,
                prompt="large clinical context fixture (not real user data)",
                request_id="cb35-fixture",
            )
        ) as stream:
            assert (await anext(stream)).type == "send_attempted"
            assert (await anext(stream)).type == "submitted"
        assert button.is_enabled.await_count == 3
        button.click.assert_awaited_once()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_send_never_enabled_fails_closed_with_zero_clicks():
    transport, button, editor = fake_page(enabled=False)
    transport.send_preflight_timeout_seconds = 0.02
    try:
        async with aclosing(
            transport.generate(
                session_id="medical-test", conversation_id=None,
                prompt="test attachment disabled forever", request_id="cb35-disabled",
            )
        ) as stream:
            with pytest.raises(RuntimeError, match="native Send control unavailable"):
                await anext(stream)
        button.click.assert_not_awaited()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_native_send_preflight_selector_only_waits_no_interaction():
    transport, button, editor = fake_page()
    button.is_enabled = AsyncMock(side_effect=[False, True])
    try:
        # Preflight must not click itself, must only observe one native candidate.
        source, selected = await BrowserTransport._native_send_control(
            transport._new_page.return_value, timeout_seconds=3.0)
        assert selected is button
        assert source == "role"
        button.click.assert_not_awaited()
        editor.press.assert_not_awaited()
    finally:
        await transport.close()


def test_preflight_timeout_environment_bounds(monkeypatch):
    for value in ("0", "91", "-1"):
        monkeypatch.setenv("CHATBRIDGE_SEND_PREFLIGHT_TIMEOUT", value)
        with pytest.raises(ValueError, match="SEND_PREFLIGHT_TIMEOUT"):
            Settings.from_env().validate()
    monkeypatch.setenv("CHATBRIDGE_SEND_PREFLIGHT_TIMEOUT", "25")
    settings = Settings.from_env()
    settings.validate()
    assert settings.send_preflight_timeout_seconds == 25.0
