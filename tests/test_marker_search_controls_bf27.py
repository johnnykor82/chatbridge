"""Offline regression tests for robust normal ChatGPT search UI activation."""
import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


class FakeNode:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def count(self):
        if self.kind in ("dialog", "input"):
            return 1 if self.page.open else 0
        if self.kind == "button":
            return 1 if self.page.show_button else 0
        return 0

    async def is_visible(self):
        return bool(await self.count())

    def locator(self, selector):
        if "input" in selector:
            return FakeNode(self.page, "input")
        return FakeNode(self.page, "none")

    def get_by_role(self, role, **kwargs):
        return FakeNode(self.page, "input" if role == "searchbox" else "none")

    async def click(self, **kwargs):
        self.page.clicks += 1
        if self.page.timeout_click:
            raise PlaywrightTimeoutError("Target did not become clickable")
        self.page.open = True


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        self.page.shortcuts.append(key)
        if self.page.keyboard_works:
            self.page.open = True


class FakePage:
    def __init__(self, *, open=False, timeout_click=False, show_button=True, keyboard_works=True):
        self.open = open
        self.timeout_click = timeout_click
        self.show_button = show_button
        self.keyboard_works = keyboard_works
        self.clicks = 0
        self.shortcuts = []
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector):
        if selector == '[role="dialog"]':
            return FakeNode(self, "dialog")
        if "button" in selector:
            return FakeNode(self, "button")
        return FakeNode(self, "none")

    def get_by_role(self, role, **kwargs):
        return FakeNode(self, "button" if role == "button" else "none")

    async def wait_for_timeout(self, ms):
        pass


@pytest.mark.asyncio
async def test_search_control_reuses_visible_dialog_without_click():
    from chatbridge.marker_lookup import activate_chat_search

    page = FakePage(open=True)
    field = await activate_chat_search(page)
    assert field is not None
    assert page.clicks == 0
    assert page.shortcuts == []


@pytest.mark.asyncio
async def test_search_control_click_timeout_uses_one_normal_keyboard_shortcut():
    from chatbridge.marker_lookup import activate_chat_search

    page = FakePage(timeout_click=True)
    field = await activate_chat_search(page)
    assert field is not None
    assert page.shortcuts == ['Meta+k']
    assert page.clicks >= 1


@pytest.mark.asyncio
async def test_search_control_fails_closed_if_no_dialog_appears():
    from chatbridge.marker_lookup import activate_chat_search

    page = FakePage(show_button=False, keyboard_works=False)
    with pytest.raises(RuntimeError, match="search"):
        await activate_chat_search(page)
    assert page.shortcuts == ['Meta+k']
