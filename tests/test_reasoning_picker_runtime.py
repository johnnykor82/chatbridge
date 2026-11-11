"""CB-16: live UI attribute preflight before expensive reasoning slider menu."""
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from chatbridge.browser_transport import BrowserTransport


class Locator:
    def __init__(self, *, count=1, attribute=None, timeout_error=False):
        self.first = self
        self.count = AsyncMock(return_value=count)
        self.get_attribute = AsyncMock(return_value=attribute)
        self.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("slider unavailable") if timeout_error else None)
        self.evaluate = AsyncMock()
        self.focus = AsyncMock()
        self.press = AsyncMock()
        self.locator = Mock(return_value=self)


def page_with_picker(*, current="high", slider_value="2", missing_slider=False):
    editor = Locator()
    picker = Locator(attribute=current)
    control = Locator(timeout_error=missing_slider)
    value = Locator(attribute=slider_value)
    control.locator = Mock(return_value=value)
    page = Mock()
    page.locator = Mock(side_effect=lambda selector: {
        'div[contenteditable="true"].ProseMirror': editor,
        'button[data-composer-navigation-target="reasoning"]': picker,
        '[data-reasoning-slider]': control
    }[selector])
    page.keyboard = Mock()
    page.keyboard.press = AsyncMock()
    page.reload = AsyncMock()
    return page, editor, picker, control, value


@pytest.mark.asyncio
async def test_selected_high_does_not_open_model_menu_when_slider_not_mounted():
    page, _, picker, control, _ = page_with_picker(current="high", missing_slider=True)
    transport = object.__new__(BrowserTransport)
    await transport._configure_reasoning_effort(page, "high")
    picker.get_attribute.assert_awaited_with("data-selected-reasoning-effort")
    picker.evaluate.assert_not_awaited()
    control.wait_for.assert_not_awaited()
    page.keyboard.press.assert_not_awaited()


@pytest.mark.asyncio
async def test_different_effort_opens_menu_and_allows_slow_slider_mount():
    page, _, picker, control, value = page_with_picker(current="low", slider_value="2")
    transport = object.__new__(BrowserTransport)
    await transport._configure_reasoning_effort(page, "high")
    picker.evaluate.assert_awaited_once()
    control.wait_for.assert_awaited_once_with(state="visible", timeout=18000)
    value.get_attribute.assert_awaited()
    page.keyboard.press.assert_awaited_once_with("Escape")


@pytest.mark.asyncio
async def test_different_effort_missing_slider_still_blocks_before_send():
    page, _, picker, control, _ = page_with_picker(current="low", missing_slider=True)
    transport = object.__new__(BrowserTransport)
    with pytest.raises(PlaywrightTimeoutError):
        await transport._configure_reasoning_effort(page, "high")
    assert picker.evaluate.await_count == 2  # one bounded reopen before Send
    assert control.wait_for.await_count == 2
    assert page.keyboard.press.await_count == 2
