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
