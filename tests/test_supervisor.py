"""Offline tests for an independently owned Chrome process and readiness API."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.browser_supervisor import ChromeSupervisor, ProfileInUse
from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store


def test_profile_isolation_and_lock(tmp_path):
    binary = tmp_path / "fake-chrome"
    binary.write_text("#!/bin/sh\n")
    supervisor = ChromeSupervisor(tmp_path / "profile", binary)
    supervisor._acquire_profile()
    try:
        assert supervisor.profile.is_dir()
        assert supervisor.profile.stat().st_mode & 0o777 == 0o700
        assert supervisor.status()["owned"] is True
        with pytest.raises(ProfileInUse):
            ChromeSupervisor(tmp_path / "profile", binary)._acquire_profile()
    finally:
        import fcntl

        fcntl.flock(supervisor._lockfile.fileno(), fcntl.LOCK_UN)
        supervisor._lockfile.close()
        supervisor._lockfile = None
    next_one = ChromeSupervisor(tmp_path / "profile", binary)
    next_one._acquire_profile()
    assert next_one.status()["owned"] is True
    next_one._lockfile.close()


def test_cdp_port_is_discovered_inside_owned_profile(tmp_path):
    file = tmp_path / "DevToolsActivePort"
    assert ChromeSupervisor._endpoint(tmp_path) is None
    file.write_text("55555\n/devtools/browser/known-id\n")
    assert ChromeSupervisor._endpoint(tmp_path) == (
        "http://127.0.0.1:55555",
        "/devtools/browser/known-id",
    )
    for invalid in (
        "9335\nhttps://foreign.invalid\n",
        "abc\n/devtools/browser/x\n",
        "0\n/devtools/browser/x\n",
        "55555\n/devtools/page/x\n",
    ):
        file.write_text(invalid)
        assert ChromeSupervisor._endpoint(tmp_path) is None


def test_new_install_defaults_to_managed_existing_explicit_cdp_keeps_external(monkeypatch):
    monkeypatch.delenv("CHATBRIDGE_BROWSER_MODE", raising=False)
    monkeypatch.delenv("CHATBRIDGE_CDP", raising=False)
    assert Settings.from_env().browser_mode == "managed"
    monkeypatch.setenv("CHATBRIDGE_CDP", "http://127.0.0.1:9335")
    assert Settings.from_env().browser_mode == "external"
    monkeypatch.setenv("CHATBRIDGE_BROWSER_MODE", "managed")
    assert Settings.from_env().browser_mode == "managed"
    monkeypatch.setenv("CHATBRIDGE_BROWSER_MODE", "nonsense")
    with pytest.raises(ValueError):
        Settings.from_env().validate()


@pytest.mark.asyncio
async def test_mock_readiness_and_status_api(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    transport = MockTransport()
    engine = GatewayEngine(store, transport)
    await engine.start()
    app = make_app(store, engine, Settings(transport="mock"))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        a = await client.get("/ready")
        assert a.status == 200
        body = await a.json()
        assert body["ready"] is True
        assert body["browser"]["mode"] == "mock"
        b = await client.get("/v1/system/status")
        assert b.status == 200
        assert (await b.json())["transport"] == "mock"
    finally:
        await client.close()
        await engine.stop()
        store.close()


@pytest.mark.asyncio
async def test_unavailable_owned_browser_returns_503(tmp_path):
    class FakeSupervisor:
        cdp_url = None

        def status(self):
            return {"mode": "managed", "state": "unavailable", "cdp_connected": False}

    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, MockTransport())
    await engine.start()
    app = make_app(
        store, engine, Settings(transport="browser"), browser_supervisor=FakeSupervisor()
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/ready")
        assert response.status == 503
        content = await response.json()
        assert content["reason"] == "browser_unavailable"
        assert content["chatgpt_authenticated"] is False
    finally:
        await client.close()
        await engine.stop()
        store.close()


def test_orphan_adoption_requires_exact_browser_identity_and_cdp_path(tmp_path, monkeypatch):
    chrome = tmp_path / "chromium"
    chrome.write_text("test")
    supervisor = ChromeSupervisor(tmp_path / "own-profile", chrome)
    supervisor.profile.mkdir(mode=0o700)
    identity = {
        "pid": 9001,
        "start": "Sun Oct 25 10:00:00 2026",
        "profile": str(supervisor.profile),
        "chrome": str(chrome),
    }
    monkeypatch.setattr(supervisor, "_identity", lambda pid: identity if pid == 9001 else None)
    record = {**identity, "cdp_path": "/devtools/browser/expected"}
    supervisor._owner_file().write_text(__import__("json").dumps(record))
    assert supervisor._verified_orphan("/devtools/browser/expected") == 9001
    assert supervisor._verified_orphan("/devtools/browser/another") is None
    record["start"] = "different process lifetime"
    supervisor._owner_file().write_text(__import__("json").dumps(record))
    assert supervisor._verified_orphan("/devtools/browser/expected") is None
