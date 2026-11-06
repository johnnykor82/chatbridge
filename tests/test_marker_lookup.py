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


@pytest.mark.asyncio
async def test_recent_chat_discovery_requires_verified_exact_marker(monkeypatch):
    from chatbridge import marker_lookup
    urls = [CHAT_URL, 'https://chatgpt.com/c/22222222-2222-4222-8222-222222222222']
    calls = []
    async def enumerate_urls(*args, **kwargs):
        return urls
    async def verified(browser, marker, url, *, observe, new_page):
        calls.append(url)
        return 'assistant_message' if url == CHAT_URL else None
    monkeypatch.setattr(marker_lookup, 'enumerate_recent_chat_urls', enumerate_urls)
    monkeypatch.setattr(marker_lookup, 'verify_candidate_chat', verified)
    result = await marker_lookup.resolve_in_recent(object(), MARKER)
    assert result == {'chat_url': CHAT_URL, 'evidence_verified': True,
                      'verification': 'assistant_message', 'matches': 1}
    assert calls == urls


@pytest.mark.asyncio
async def test_recent_discovery_fails_closed_for_multiple_verified_chats(monkeypatch):
    from chatbridge import marker_lookup
    urls = [CHAT_URL, 'https://chatgpt.com/c/22222222-2222-4222-8222-222222222222']
    async def enumerate_urls(*args, **kwargs):
        return urls
    async def verified(browser, marker, url, *, observe, new_page):
        return 'assistant_message'
    monkeypatch.setattr(marker_lookup, 'enumerate_recent_chat_urls', enumerate_urls)
    monkeypatch.setattr(marker_lookup, 'verify_candidate_chat', verified)
    with pytest.raises(AmbiguousMarker):
        await marker_lookup.resolve_in_recent(object(), MARKER)


@pytest.mark.asyncio
async def test_automatic_discovery_prefers_recent_without_index(monkeypatch):
    from chatbridge import marker_lookup
    calls = []
    async def recent(*args, **kwargs):
        calls.append('recent')
        return {'chat_url': CHAT_URL, 'verification': 'assistant_message',
                'evidence_verified': True, 'matches': 1}
    async def index(*args, **kwargs):
        calls.append('index')
        raise AssertionError('Index must not be used if recent chat verifies marker')
    monkeypatch.setattr(marker_lookup, 'resolve_in_recent', recent)
    monkeypatch.setattr(marker_lookup, 'resolve_in_browser', index)
    assert (await marker_lookup.resolve_automatically(object(), MARKER))['chat_url'] == CHAT_URL
    assert calls == ['recent']


@pytest.mark.asyncio
async def test_automatic_discovery_falls_back_to_index_only_on_recent_not_found(monkeypatch):
    from chatbridge import marker_lookup
    calls = []
    async def recent(*args, **kwargs):
        calls.append('recent')
        raise MarkerNotFound('No recent sidebar match')
    async def index(*args, **kwargs):
        calls.append('index')
        return {'chat_url': CHAT_URL, 'verification': 'assistant_message',
                'evidence_verified': True, 'matches': 1}
    monkeypatch.setattr(marker_lookup, 'resolve_in_recent', recent)
    monkeypatch.setattr(marker_lookup, 'resolve_in_browser', index)
    assert (await marker_lookup.resolve_automatically(object(), MARKER))['chat_url'] == CHAT_URL
    assert calls == ['recent', 'index']


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
