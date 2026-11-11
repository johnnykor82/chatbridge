"""P06D: transient reasoning-slider menu loss is recoverable only before Send."""
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from test_reasoning_picker_runtime import page_with_picker

from chatbridge.browser_transport import BrowserTransport


@pytest.mark.asyncio
async def test_transient_missing_slider_reopens_menu_once_then_verifies_target():
    page, _, picker, control, value = page_with_picker(current="low", slider_value="2")
    control.wait_for = AsyncMock(side_effect=[PlaywrightTimeoutError("first menu lost"), None])
    transport = object.__new__(BrowserTransport)
    await transport._configure_reasoning_effort(page, "high")
    assert picker.evaluate.await_count == 2  # initial + bounded pre-Send reopen
    assert control.wait_for.await_count == 2
    assert value.get_attribute.await_count == 2
    assert page.keyboard.press.await_count == 2  # dismiss transient menu + final cleanup


@pytest.mark.asyncio
async def test_repeated_missing_slider_fails_closed_after_one_reopen():
    page, _, picker, control, _ = page_with_picker(current="low", missing_slider=True)
    transport = object.__new__(BrowserTransport)
    with pytest.raises(PlaywrightTimeoutError):
        await transport._configure_reasoning_effort(page, "high")
    assert picker.evaluate.await_count == 2
    assert control.wait_for.await_count == 2
    assert page.keyboard.press.await_count == 2


@pytest.mark.asyncio
async def test_verified_already_selected_never_clicks_or_waits_on_slider():
    page, _, picker, control, _ = page_with_picker(current="high", missing_slider=True)
    transport = object.__new__(BrowserTransport)
    await transport._configure_reasoning_effort(page, "high")
    picker.evaluate.assert_not_awaited()
    control.wait_for.assert_not_awaited()
