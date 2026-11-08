"""BF30 P06a: indexed candidate verification without opening rendered chat pages."""
import pytest

from chatbridge import marker_lookup
from chatbridge.rate_limit import ProviderRateLimitError

MARKER = "WC9d4f8a2c7b61e305"
CID_A = "11111111-1111-4111-8111-111111111111"
CID_B = "22222222-2222-4222-8222-222222222222"
CHAT_A = "https://chatgpt.com/c/" + CID_A


def history(cid, marker=MARKER, role="assistant"):
    return {"provider_conversation_id": cid, "conversation_id": cid,
            "chain": [{"role": role, "content": "Exact " + marker}]}


class HistoryPage:
    def __init__(self, data):
        self.data = data
        self.history_calls = []

    async def evaluate(self, code, cid):
        self.history_calls.append(cid)
        return self.data


@pytest.mark.asyncio
async def test_indexed_verifier_checks_provider_identity_full_marker_and_role():
    page = HistoryPage(history(CID_A))
    assert await marker_lookup.verify_index_candidate_history(page, CHAT_A, MARKER) == "assistant_message"
    assert page.history_calls == [CID_A]


@pytest.mark.asyncio
async def test_indexed_verifier_rejects_wrong_provider_conversation_id():
    page = HistoryPage(history(CID_B))
    with pytest.raises(marker_lookup.RecentScanUnavailable, match="identity mismatch"):
        await marker_lookup.verify_index_candidate_history(page, CHAT_A, MARKER)


@pytest.mark.asyncio
async def test_indexed_verifier_rejects_prefix_and_wrong_role():
    page = HistoryPage(history(CID_A, MARKER + "-suffix", role="assistant"))
    assert await marker_lookup.verify_index_candidate_history(page, CHAT_A, MARKER) is None
    page = HistoryPage(history(CID_A, MARKER, role="tool"))
    assert await marker_lookup.verify_index_candidate_history(page, CHAT_A, MARKER) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    ("history_http_429", ProviderRateLimitError),
    ("history_http_401", PermissionError),
    ("history_http_403", PermissionError),
    ("history_http_503", marker_lookup.RecentScanUnavailable),
])
async def test_indexed_verifier_fails_closed_on_rate_limit_auth_and_unavailable(error, expected):
    page = HistoryPage({"error": error})
    with pytest.raises(expected):
        await marker_lookup.verify_index_candidate_history(page, CHAT_A, MARKER)


class SearchLinks:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return 2 if self.page.query else 6

    def nth(self, index):
        class Link:
            async def get_attribute(_, name):
                assert name == "href"
                return "/c/" + (CID_A if index == 0 else CID_B)
        return Link()


class SearchDialog:
    def __init__(self, page):
        self.page = page

    @property
    def last(self):
        return self

    async def count(self):
        return 1

    def locator(self, selector):
        return SearchLinks(self.page)


class SearchField:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def fill(self, marker, **kwargs):
        self.page.query = marker


class IndexedPage:
    def __init__(self):
        self.url = "about:blank"
        self.query = ""
        self.calls = []
        self.closed = False

    async def goto(self, url, **kwargs):
        self.url = url

    def locator(self, selector):
        assert selector == '[role="dialog"]'
        return SearchDialog(self)

    async def wait_for_timeout(self, ms):
        pass

    async def evaluate(self, js, *args):
        if not args:
            return {"ready": "complete", "sidebar_links": 18,
                    "search_buttons": 2, "visible_search_buttons": 1, "dialogs": 1,
                    "visible_comboboxes": 1, "dialog_links": 2}
        cid = args[0]
        self.calls.append(cid)
        return history(cid)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_index_search_verifies_all_candidates_on_single_authenticated_page(monkeypatch):
    page = IndexedPage()
    new_page_calls = []

    async def create(browser):
        new_page_calls.append("create")
        return page

    async def activate(page):
        return SearchField(page)

    monkeypatch.setattr(marker_lookup, "activate_chat_search", activate)
    browser = type("Browser", (), {"contexts": [object()]})()
    with pytest.raises(marker_lookup.AmbiguousMarker):
        await marker_lookup.resolve_in_browser(browser, MARKER, new_page=create)
    assert page.query == MARKER
    assert page.calls == [CID_A, CID_B]
    assert new_page_calls == ["create"], "No candidate-page navigation should occur"
    assert page.closed
