"""BF29: exact marker search precedes reading suggestions; safe DOM telemetry."""
import logging

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from chatbridge import marker_lookup

MARKER = "WC9d4f8a2c7b61e305"
CHAT_URL = "https://chatgpt.com/c/11111111-1111-4111-8111-111111111111"


class Field:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def fill(self, value, **_):
        self.page.calls.append("fill")
        self.page.query = value

    async def count(self):
        return 1

    async def is_visible(self):
        return True


class SearchLinks:
    def __init__(self, page):
        self.page = page

    async def count(self):
        self.page.calls.append("links_after_fill" if self.page.query else "links_before_fill")
        return 1 if self.page.query else 6

    def nth(self, _index):
        return self

    async def get_attribute(self, key):
        assert key == "href"
        return CHAT_URL.removeprefix("https://chatgpt.com")


class Dialog:
    def __init__(self, page):
        self.page = page

    @property
    def last(self):
        return self

    async def count(self):
        return 1 if self.page.open else 0

    async def is_visible(self):
        return self.page.open

    def locator(self, selector):
        return SearchLinks(self.page) if 'a[href^=' in selector else self.page.field

    def get_by_role(self, _role):
        return Empty()


class Empty:
    @property
    def first(self):
        return self

    async def count(self):
        return 0


class SearchPage:
    def __init__(self):
        self.query = None
        self.open = True
        self.url = "about:blank"
        self.calls = []
        self.field = Field(self)

    async def goto(self, url, **_):
        self.url = url

    async def evaluate(self, code, *args):
        if args:
            cid = args[0]
            return {"provider_conversation_id": cid, "conversation_id": cid,
                    "chain": [{"role": "assistant",
                               "content": "Existing assistant posted " + MARKER}]}
        return {"ready": "complete", "sidebar_links": 18,
                "search_buttons": 2, "visible_search_buttons": 1,
                "dialogs": 1, "visible_comboboxes": 1, "dialog_links": 1}

    def locator(self, selector):
        assert selector == '[role="dialog"]'
        return Dialog(self)

    async def wait_for_timeout(self, _):
        pass

    async def close(self):
        pass


class Role:
    def __init__(self, is_assistant):
        self.is_assistant = is_assistant

    async def count(self):
        return 1 if self.is_assistant else 0

    async def all_inner_texts(self):
        return ["Existing assistant posted " + MARKER] if self.is_assistant else []


class ChatPage:
    def __init__(self):
        self.url = "about:blank"

    async def goto(self, url, **_):
        self.url = url

    def locator(self, selector):
        return Role('assistant' in selector)

    async def wait_for_timeout(self, _):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_search_enters_full_marker_before_counting_suggestions(monkeypatch):
    """Old beta20 counted six recent chats and raised AmbiguousMarker before fill."""
    search = SearchPage()
    chat = ChatPage()
    pages = [search, chat]

    async def create(_browser):
        return pages.pop(0)

    async def dialog_input(_page):
        return search.field

    monkeypatch.setattr(marker_lookup, "activate_chat_search", dialog_input)
    browser = type("Browser", (), {"contexts": [object()]})()
    result = await marker_lookup.resolve_in_browser(browser, MARKER, new_page=create)
    assert result == {
        "chat_url": CHAT_URL, "evidence_verified": True,
        "verification": "assistant_message", "matches": 1,
    }
    assert search.query == MARKER
    assert "links_before_fill" not in search.calls
    assert search.calls.index("fill") < search.calls.index("links_after_fill")


class ClickButton:
    def __init__(self, page, index):
        self.page, self.index = page, index

    async def is_visible(self):
        return True

    async def click(self, **_):
        self.page.clicked.append(self.index)
        if self.index == 0:
            raise PlaywrightTimeoutError("First matching button covered")
        self.page.open = True


class Buttons:
    def __init__(self, page, count):
        self.page, self.number = page, count

    async def count(self):
        return self.number

    @property
    def first(self):
        return ClickButton(self.page, 0)

    def nth(self, index):
        return ClickButton(self.page, index)


class EmptyKeyboard:
    def __init__(self):
        self.keys = []

    async def press(self, key):
        self.keys.append(key)


class MultiSearchPage(SearchPage):
    def __init__(self):
        super().__init__()
        self.open = False
        self.clicked = []
        self.keyboard = EmptyKeyboard()

    def get_by_role(self, role, **_):
        return Buttons(self, 2) if role == "button" else Empty()

    def locator(self, selector):
        if selector == '[role="dialog"]':
            return Dialog(self)
        return Empty()


@pytest.mark.asyncio
async def test_search_tries_second_visible_button_when_first_is_covered():
    page = MultiSearchPage()
    field = await marker_lookup.activate_chat_search(page)
    assert field is page.field
    assert page.clicked == [0, 1]
    assert page.keyboard.keys == []


@pytest.mark.asyncio
async def test_redacted_telemetry_excludes_page_secrets_and_only_logs_counts(caplog):
    class FakeDOM:
        async def evaluate(self, expression):
            assert "querySelectorAll" in expression
            return {
                "ready": "complete", "sidebar_links": 22,
                "search_buttons": 2, "visible_search_buttons": 1,
                "dialogs": 1, "visible_comboboxes": 1, "dialog_links": 6,
                "conversation_url": "SECRET_URL_DO_NOT_LOG",
                "marker": "SECRET_MARKER_DO_NOT_LOG",
                "text": "SECRET_CHAT_TEXT_DO_NOT_LOG",
            }
    with caplog.at_level(logging.WARNING):
        state = await marker_lookup.log_search_dom(FakeDOM(), "search_dialog_missing")
    assert state["sidebar_links"] == 22
    assert state["dialog_links"] == 6
    assert set(state).issubset({
        "ready", "sidebar_links", "search_buttons", "visible_search_buttons",
        "dialogs", "visible_comboboxes", "dialog_links",
    })
    for secret in ("SECRET_URL_DO_NOT_LOG", "SECRET_MARKER_DO_NOT_LOG",
                   "SECRET_CHAT_TEXT_DO_NOT_LOG"):
        assert secret not in str(state)
        assert secret not in caplog.text


class UnfilteredLinks(SearchLinks):
    async def count(self):
        self.page.calls.append("unfiltered_after_fill")
        return 6


class UnfilteredDialog(Dialog):
    def locator(self, selector):
        return UnfilteredLinks(self.page) if 'a[href^=' in selector else self.page.field


class UnfilteredSearchPage(SearchPage):
    def locator(self, selector):
        assert selector == '[role="dialog"]'
        return UnfilteredDialog(self)


@pytest.mark.asyncio
async def test_stale_recent_chat_suggestions_never_become_verified_results(monkeypatch):
    page = UnfilteredSearchPage()
    browser = type("Browser", (), {"contexts": [object()]})()

    async def create(_):
        return page

    async def field(_):
        return page.field

    monkeypatch.setattr(marker_lookup, "activate_chat_search", field)
    with pytest.raises(RuntimeError, match="unfiltered"):
        await marker_lookup.resolve_in_browser(browser, MARKER, new_page=create)
    assert page.query == MARKER
    assert page.calls.count("unfiltered_after_fill") >= 1


@pytest.mark.asyncio
async def test_dom_telemetry_failure_remains_private_and_never_breaks_lookup(caplog):
    class UnavailableDOM:
        async def evaluate(self, _):
            raise RuntimeError("PRIVATE_CHROME_PROFILE_SECRET")
    with caplog.at_level(logging.WARNING):
        state = await marker_lookup.log_search_dom(
            UnavailableDOM(), "search_dialog_missing"
        )
    assert state == {"ready": "unavailable"}
    assert "PRIVATE_CHROME_PROFILE_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_wait_for_hydrated_search_controls_before_clicking(monkeypatch):
    """Live MacServer changed 0/1 sidebar/button to 18/2 while Search was clicked."""
    events = []
    page = SearchPage()
    conversation = ChatPage()
    pages = [page, conversation]

    async def next_page(_):
        return pages.pop(0)

    async def ready(_):
        events.append("ready")
        return True

    async def activate(_):
        events.append("activate")
        return page.field

    monkeypatch.setattr(marker_lookup, "wait_for_search_controls_ready", ready, raising=False)
    monkeypatch.setattr(marker_lookup, "activate_chat_search", activate)
    browser = type("Browser", (), {"contexts": [object()]})()
    result = await marker_lookup.resolve_in_browser(browser, MARKER, new_page=next_page)
    assert result["verification"] == "assistant_message"
    assert events[:2] == ["ready", "activate"]


@pytest.mark.asyncio
async def test_hydration_waits_for_sidebar_and_second_search_control():
    class HydratingPage:
        def __init__(self):
            self.step = 0
            self.waits = 0

        async def evaluate(self, _script):
            return {
                "ready": "interactive",
                "sidebar_links": 0 if self.step < 2 else 18,
                "search_buttons": 1 if self.step < 2 else 2,
                "visible_search_buttons": 1,
                "dialogs": 0,
                "visible_comboboxes": 0,
                "dialog_links": 0,
            }

        async def wait_for_timeout(self, _):
            self.waits += 1
            self.step += 1

    page = HydratingPage()
    assert await marker_lookup.wait_for_search_controls_ready(page) is True
    assert page.waits == 2


@pytest.mark.asyncio
async def test_hydration_timeout_is_bounded_and_never_bypasses_browser():
    class NoSidebar:
        def __init__(self):
            self.waits = 0

        async def evaluate(self, _):
            return {
                "ready": "interactive", "sidebar_links": 0,
                "search_buttons": 1, "visible_search_buttons": 1,
                "dialogs": 0, "visible_comboboxes": 0, "dialog_links": 0,
            }

        async def wait_for_timeout(self, _):
            self.waits += 1

    page = NoSidebar()
    assert await marker_lookup.wait_for_search_controls_ready(page) is False
    assert 2 <= page.waits <= 12
