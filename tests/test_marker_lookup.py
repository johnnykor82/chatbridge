"""No real ChatGPT account traffic: marker parsing and HTTP contract."""
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.marker_lookup import (
    AmbiguousMarker,
    MarkerNotFound,
    canonical_candidate,
    user_message_contains_marker,
    validate_marker,
)
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store

MARKER = 'WATCHER-CHAT-' + 'abcdef1234567890' * 2
CHAT_URL = 'https://chatgpt.com/c/11111111-1111-4111-8111-111111111111'
PROJECT = 'g-p-' + 'a' * 32


def test_marker_validation_and_user_role_evidence():
    assert validate_marker(MARKER) == MARKER
    for bad in ('a' * 32, 'WATCHER-CHAT-' + 'g' * 32, MARKER + 'X', MARKER.upper(), ''):
        with pytest.raises(ValueError):
            validate_marker(bad)
    assert user_message_contains_marker('This is ' + MARKER + ' in my message', MARKER)
    assert not user_message_contains_marker(MARKER + '-tail', MARKER)
    assert not user_message_contains_marker('x' + MARKER, MARKER)


def test_url_security_and_project_urls():
    assert canonical_candidate('/c/11111111-1111-4111-8111-111111111111') == CHAT_URL
    assert canonical_candidate('/g/' + PROJECT + '-workspace/c/11111111-1111-4111-8111-111111111111') == (
        'https://chatgpt.com/g/' + PROJECT + '/c/11111111-1111-4111-8111-111111111111'
    )
    for bad in ('https://evil.com/c/11111111-1111-4111-8111-111111111111',
                '/c/11111111-1111-4111-8111-111111111111?foo=1',
                'http://chatgpt.com/c/11111111-1111-4111-8111-111111111111',
                '/share/11111111-1111-4111-8111-111111111111'):
        assert canonical_candidate(bad) is None


@pytest.mark.asyncio
async def test_lookup_http_contract(tmp_path: Path):
    store = Store(tmp_path / 'test.db')
    transport = MockTransport()
    engine = GatewayEngine(store, transport)
    await engine.start()
    client = TestClient(TestServer(make_app(store, engine, Settings(transport='mock'))))
    await client.start_server()
    try:
        assert (await client.post('/v1/chats/resolve-marker', json={'marker': 'bad'})).status == 400
        assert (await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})).status == 501

        async def ok(marker):
            return {'chat_url': CHAT_URL, 'evidence_verified': True,
                    'verification': 'user_message', 'matches': 1}
        transport.resolve_chat_marker = ok
        response = await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})
        assert response.status == 200
        assert (await response.json())['chat_url'] == CHAT_URL

        async def absent(marker):
            raise MarkerNotFound()
        transport.resolve_chat_marker = absent
        assert (await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})).status == 404

        async def duplicated(marker):
            raise AmbiguousMarker()
        transport.resolve_chat_marker = duplicated
        assert (await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})).status == 409

        async def unverified(marker):
            return {'chat_url': CHAT_URL, 'evidence_verified': False}
        transport.resolve_chat_marker = unverified
        assert (await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})).status == 502

        async def forged(marker):
            return {'chat_url': 'https://evil.invalid/c/1', 'evidence_verified': True,
                    'verification': 'user_message', 'matches': 1}
        transport.resolve_chat_marker = forged
        assert (await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})).status == 502
    finally:
        await client.close()
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_assistant_verification_api(tmp_path: Path):
    store = Store(tmp_path / 'assistant.db')
    transport = MockTransport()
    async def verified_assistant(marker):
        return {'chat_url': CHAT_URL, 'evidence_verified': True,
                'verification': 'assistant_message', 'matches': 1}
    transport.resolve_chat_marker = verified_assistant
    engine = GatewayEngine(store, transport)
    await engine.start()
    client = TestClient(TestServer(make_app(store, engine, Settings(transport='mock'))))
    await client.start_server()
    try:
        response = await client.post('/v1/chats/resolve-marker', json={'marker': MARKER})
        assert response.status == 200
        assert (await response.json())['verification'] == 'assistant_message'
    finally:
        await client.close()
        await engine.stop()
        store.close()


def test_recent_chat_candidate_selection_safe_and_bounded():
    from chatbridge.marker_lookup import recent_chat_candidates
    raw = [
        '/c/11111111-1111-4111-8111-111111111111',
        '/c/11111111-1111-4111-8111-111111111111',
        '/c/22222222-2222-4222-8222-222222222222',
        'https://evil.invalid/c/11111111-1111-4111-8111-111111111111',
        '/share/11111111-1111-4111-8111-111111111111',
        '/c/33333333-3333-4333-8333-333333333333?foo=1',
    ]
    found = recent_chat_candidates(raw, limit=2)
    assert found == [
        CHAT_URL,
        'https://chatgpt.com/c/22222222-2222-4222-8222-222222222222',
    ]


class RecentSidebarPage:
    def __init__(self, history):
        self.history = history
        self.hrefs = ['/c/' + cid for cid in history]
        self.loaded = []
        self.url = "https://chatgpt.com/"
        self.closed = False

    def locator(self, selector):
        assert 'a[href^="/c/"]' in selector
        return self

    async def count(self):
        return len(self.hrefs)

    def nth(self, index):
        class Link:
            async def get_attribute(_, attribute):
                assert attribute == "href"
                return self.hrefs[index]
        return Link()

    async def evaluate(self, js, *args):
        if not args:
            return {'ids': list(self.history)}
        return self.history[args[0]]

    async def wait_for_timeout(self, ms):
        pass

    async def goto(self, url, **kwargs):
        self.loaded.append(url)
        self.url = url

    def on(self, *args):
        pass

    async def close(self):
        self.closed = True


def recent_history(cid, marker=None):
    return {'provider_conversation_id': cid, 'conversation_id': cid,
            'chain': [{'role': 'assistant', 'content': marker or 'unrelated'}]}


@pytest.mark.asyncio
async def test_recent_chat_discovery_requires_verified_exact_marker():
    from chatbridge.marker_lookup import resolve_recent_page
    one = CHAT_URL.rsplit('/c/', 1)[-1]
    two = '22222222-2222-4222-8222-222222222222'
    page = RecentSidebarPage({
        one: recent_history(one, MARKER),
        two: recent_history(two),
    })
    result = await resolve_recent_page(page, MARKER)
    assert result == {'chat_url': CHAT_URL, 'evidence_verified': True,
                      'verification': 'assistant_message', 'matches': 1}
    assert page.loaded == []


@pytest.mark.asyncio
async def test_recent_discovery_fails_closed_for_multiple_verified_chats():
    from chatbridge.marker_lookup import resolve_recent_page
    one = CHAT_URL.rsplit('/c/', 1)[-1]
    two = '22222222-2222-4222-8222-222222222222'
    page = RecentSidebarPage({
        one: recent_history(one, MARKER),
        two: recent_history(two, MARKER),
    })
    with pytest.raises(AmbiguousMarker):
        await resolve_recent_page(page, MARKER)


@pytest.mark.asyncio
async def test_recent_discovery_uses_one_navigation_for_all_chats():
    from chatbridge.marker_lookup import resolve_in_recent
    one = CHAT_URL.rsplit('/c/', 1)[-1]
    two = '22222222-2222-4222-8222-222222222222'
    page = RecentSidebarPage({
        one: recent_history(one, MARKER), two: recent_history(two),
    })
    class Browser:
        contexts = [object()]
    async def new_page(browser):
        return page
    result = await resolve_in_recent(Browser(), MARKER, new_page=new_page)
    assert result['chat_url'] == CHAT_URL
    assert page.loaded == ['https://chatgpt.com/']
    assert page.closed


@pytest.mark.asyncio
async def test_automatic_discovery_prefers_search_before_recent(monkeypatch):
    from chatbridge import marker_lookup
    calls = []

    async def recent(*args, **kwargs):
        calls.append('recent')
        raise AssertionError('Recent scan must not precede successful Search')

    async def index(*args, **kwargs):
        calls.append('index')
        return {'chat_url': CHAT_URL, 'verification': 'assistant_message',
                'evidence_verified': True, 'matches': 1}

    monkeypatch.setattr(marker_lookup, 'resolve_in_recent', recent)
    monkeypatch.setattr(marker_lookup, 'resolve_in_browser', index)
    assert (await marker_lookup.resolve_automatically(object(), MARKER))['chat_url'] == CHAT_URL
    assert calls == ['index']


@pytest.mark.asyncio
async def test_automatic_discovery_recent_fallback_only_after_search_miss(monkeypatch):
    from chatbridge import marker_lookup
    calls = []

    async def recent(*args, **kwargs):
        calls.append('recent')
        return {'chat_url': CHAT_URL, 'verification': 'assistant_message',
                'evidence_verified': True, 'matches': 1}

    async def index(*args, **kwargs):
        calls.append('index')
        raise MarkerNotFound('No indexed match')

    monkeypatch.setattr(marker_lookup, 'resolve_in_recent', recent)
    monkeypatch.setattr(marker_lookup, 'resolve_in_browser', index)
    assert (await marker_lookup.resolve_automatically(object(), MARKER))['chat_url'] == CHAT_URL
    assert calls == ['index', 'recent']


@pytest.mark.asyncio
async def test_recent_candidate_rejects_mismatched_conversation_or_marker():
    from chatbridge.marker_lookup import verify_candidate_chat

    class Locator:
        def __init__(self, texts):
            self.texts = texts
        async def count(self):
            return len(self.texts)
        async def all_inner_texts(self):
            return self.texts

    class Page:
        def __init__(self, user=None, assistant=None, redirected=None):
            self.user = user or []
            self.assistant = assistant or []
            self.redirected = redirected
            self.url = ""
            self.closed = False
        async def goto(self, url, **kwargs):
            self.url = self.redirected or url
        def locator(self, selector):
            return Locator(self.user if 'data-user-message-bubble' in selector else self.assistant)
        async def wait_for_timeout(self, t):
            pass
        async def evaluate(self, script, conversation_id):
            # No verified backend chain: reject rather than infer identity.
            return {"error": "history_unavailable"}
        async def close(self):
            self.closed = True

    class Browser:
        contexts = [object()]

    async def scan(page):
        return await verify_candidate_chat(
            Browser(), MARKER, CHAT_URL, new_page=lambda _: async_page(page)
        )
    async def async_page(page):
        return page

    redirected = Page(assistant=[MARKER], redirected='https://chatgpt.com/c/22222222-2222-4222-8222-222222222222')
    assert await scan(redirected) is None
    assert redirected.closed

    wrong = Page(assistant=['wrong marker'])
    with pytest.raises(RuntimeError, match="history verification unavailable"):
        await scan(wrong)
    assert wrong.closed

    verified = Page(assistant=['Some context ' + MARKER])
    assert await scan(verified) == 'assistant_message'
    assert verified.closed
    both = Page(user=[MARKER], assistant=[MARKER])
    assert await scan(both) == 'assistant_message'
    assert both.closed


def test_recent_history_requires_real_provider_id_and_exact_assistant_role():
    from chatbridge.marker_lookup import verified_role_from_history
    cid = CHAT_URL.rsplit('/c/', 1)[-1]
    valid = {'provider_conversation_id': cid, 'conversation_id': cid,
             'chain': [{'role': 'assistant', 'content': 'Alpha ' + MARKER}]}
    assert verified_role_from_history(valid, cid, MARKER) == 'assistant_message'
    assert verified_role_from_history({**valid, 'chain': [{'role': 'user', 'content': MARKER}]},
                                      cid, MARKER) == 'user_message'
    assert verified_role_from_history({**valid, 'chain': [{'role': 'assistant', 'content': 'Not this'}]},
                                      cid, MARKER) is None
    with pytest.raises(RuntimeError, match='identity'):
        verified_role_from_history({**valid, 'provider_conversation_id': None}, cid, MARKER)
    with pytest.raises(RuntimeError, match='identity'):
        verified_role_from_history({**valid, 'provider_conversation_id': 'other'}, cid, MARKER)


def test_provider_recent_ids_are_bounded_canonical_candidates():
    from chatbridge.marker_lookup import provider_recent_candidates
    raw = {'items': [
        {'id': '11111111-1111-4111-8111-111111111111'},
        {'id': '11111111-1111-4111-8111-111111111111'},
        {'id': '22222222-2222-4222-8222-222222222222'},
        {'id': 'not-a-uuid'}, {'id': 'https://evil.invalid'},
    ]}
    assert provider_recent_candidates(raw, limit=2) == [
        CHAT_URL,
        'https://chatgpt.com/c/22222222-2222-4222-8222-222222222222',
    ]
    assert provider_recent_candidates({'items': []}) == []
    with pytest.raises(ValueError):
        provider_recent_candidates({'items': 'malformed'})


@pytest.mark.asyncio
async def test_provider_recent_list_429_is_never_downgraded_to_index_retry():
    from chatbridge.marker_lookup import provider_recent_chat_urls
    from chatbridge.rate_limit import ProviderRateLimitError
    class Page:
        async def evaluate(self, js):
            return {'error': 'recent_http_429', 'retry_after': '50'}
    with pytest.raises(ProviderRateLimitError):
        await provider_recent_chat_urls(Page())


@pytest.mark.asyncio
async def test_provider_recent_inventory_resolves_marker_if_sidebar_empty():
    from chatbridge.marker_lookup import resolve_recent_page
    one = CHAT_URL.rsplit('/c/', 1)[-1]
    two = '22222222-2222-4222-8222-222222222222'
    page = RecentSidebarPage({
        one: recent_history(one, MARKER), two: recent_history(two),
    })
    page.hrefs = []
    result = await resolve_recent_page(page, MARKER)
    assert result['verification'] == 'assistant_message'
    assert result['chat_url'] == CHAT_URL
    assert page.loaded == []


@pytest.mark.asyncio
async def test_provider_429_aborts_when_sidebar_is_empty():
    from chatbridge.marker_lookup import resolve_recent_page
    from chatbridge.rate_limit import ProviderRateLimitError
    class Page(RecentSidebarPage):
        async def evaluate(self, js, *args):
            if not args:
                return {'error': 'recent_http_429'}
            return await super().evaluate(js, *args)
    page = Page({})
    with pytest.raises(ProviderRateLimitError):
        await resolve_recent_page(page, MARKER)


@pytest.mark.asyncio
async def test_verified_recent_assistant_survives_unrelated_history_failure():
    from chatbridge.marker_lookup import resolve_recent_page
    one = CHAT_URL.rsplit('/c/', 1)[-1]
    two = '22222222-2222-4222-8222-222222222222'
    three = '33333333-3333-4333-8333-333333333333'
    four = '44444444-4444-4444-8444-444444444444'
    class RecentPage(RecentSidebarPage):
        def __init__(self):
            super().__init__({
                one: recent_history(one, MARKER), two: recent_history(two),
                three: {'error': 'history_http_500'},
                four: recent_history(four),
            })
            self.used = []
        async def evaluate(self, js, *args):
            if args:
                self.used.append(args[0])
            return await super().evaluate(js, *args)
    page = RecentPage()
    result = await resolve_recent_page(page, MARKER)
    assert result == {
        'chat_url': CHAT_URL, 'verification': 'assistant_message',
        'evidence_verified': True, 'matches': 1,
    }
    # A confirmed recent match must not trigger scanning the entire archive.
    assert four not in page.used


def test_short_marker_live_contract():
    from chatbridge.marker_lookup import (
        RecentScanUnavailable,
        message_contains_marker,
        verified_role_from_history,
    )

    short = 'WC4f9e8c16d7a23b50'
    assert validate_marker(short) == short
    assert message_contains_marker('prefix ' + short + ' suffix', short)
    for bad in ('WC' + 'a' * 15, 'WC' + 'a' * 17, 'wc' + 'a' * 16,
                'WC' + 'g' * 16, short.upper(), short + '-tail'):
        with pytest.raises(ValueError):
            validate_marker(bad)
    assert not message_contains_marker('x' + short, short)
    assert not message_contains_marker(short + 'z', short)
    assert not message_contains_marker(short + '-tail', short)
    cid = '11111111-1111-4111-8111-111111111111'
    payload = {'provider_conversation_id': cid,
               'chain': [{'role': 'assistant', 'content': 'Visible ' + short}]}
    assert verified_role_from_history(payload, cid, short) == 'assistant_message'
    with pytest.raises(RecentScanUnavailable):
        verified_role_from_history(payload, '22222222-2222-4222-8222-222222222222', short)


@pytest.mark.asyncio
async def test_short_marker_http_contract(tmp_path: Path):
    short = 'WC4f9e8c16d7a23b50'
    store = Store(tmp_path / 'short-lookup.db')
    transport = MockTransport()
    async def verified_assistant(marker):
        assert marker == short
        return {'chat_url': CHAT_URL, 'evidence_verified': True,
                'verification': 'assistant_message', 'matches': 1}
    transport.resolve_chat_marker = verified_assistant
    engine = GatewayEngine(store, transport)
    await engine.start()
    client = TestClient(TestServer(make_app(store, engine, Settings(transport='mock'))))
    await client.start_server()
    try:
        response = await client.post('/v1/chats/resolve-marker', json={'marker': short})
        assert response.status == 200
        assert (await response.json())['verification'] == 'assistant_message'
    finally:
        await client.close()
        await engine.stop()
        store.close()
