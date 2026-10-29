"""Managed browser idle shutdown and request lifetime regressions."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chatbridge.browser_supervisor import ChromeSupervisor
from chatbridge.browser_transport import BrowserTransport
from chatbridge.config import Settings
from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.server import make_app
from chatbridge.store import Store


@pytest.fixture
def managed(tmp_path, monkeypatch):
    binary = tmp_path / "chrome"
    binary.touch()
    supervisor = ChromeSupervisor(tmp_path / "profile", binary, check_interval=0.005,
                                  idle_timeout=0.04)
    state = {"running": False, "launches": 0, "closed": asyncio.Event()}

    async def launch():
        if not state["running"]:
            state["launches"] += 1
            state["running"] = True
            supervisor.cdp_url = f"http://127.0.0.1:{9000 + state['launches']}"

    async def terminate():
        state["running"] = False
        supervisor.cdp_url = None
        state["closed"].set()

    monkeypatch.setattr(supervisor, "_launch", launch)
    monkeypatch.setattr(supervisor, "_terminate_owned", terminate)
    monkeypatch.setattr(supervisor, "_active_pid", lambda: 123 if state["running"] else None)
    monkeypatch.setattr(supervisor, "_current_endpoint", lambda: (supervisor.cdp_url, "/devtools/browser/test"))
    monkeypatch.setattr(supervisor, "_probe", lambda *args: True)
    return supervisor, state


@pytest.mark.asyncio
async def test_idle_preserves_profile_and_watchdog_waits_for_request(managed):
    supervisor, state = managed
    await supervisor.start()
    marker = supervisor.profile / "session-cookie-fixture"
    marker.write_text("retained")
    try:
        await asyncio.wait_for(state["closed"].wait(), 1)
        assert supervisor.status()["state"] == "idle"
        assert supervisor.status()["owned"] is True
        assert marker.read_text() == "retained"
        await asyncio.sleep(0.03)
        assert state["launches"] == 1
        state["closed"].clear()
        async with supervisor.activity():
            assert state["launches"] == 2
            await asyncio.sleep(0.06)
            assert not state["closed"].is_set()
        await asyncio.wait_for(state["closed"].wait(), 1)
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_short_requests_restart_idle_deadline_and_failures_release_lease(managed):
    supervisor, state = managed
    supervisor.idle_timeout = 60
    await supervisor.start()
    try:
        supervisor._last_activity -= 10
        before = supervisor._last_activity
        with pytest.raises(ValueError):
            async with supervisor.activity():
                raise ValueError("request failed")
        assert supervisor._active_requests == 0
        assert supervisor._last_activity > before + 9
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_request_arriving_during_idle_termination_waits_then_restarts(managed, monkeypatch):
    supervisor, state = managed
    closing, finish = asyncio.Event(), asyncio.Event()
    terminate = supervisor._terminate_owned

    async def paused_termination():
        closing.set()
        await finish.wait()
        await terminate()

    monkeypatch.setattr(supervisor, "_terminate_owned", paused_termination)
    await supervisor.start()
    task = None
    try:
        await asyncio.wait_for(closing.wait(), 1)
        async def request():
            async with supervisor.activity():
                return supervisor.cdp_url
        task = asyncio.create_task(request())
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        assert await asyncio.wait_for(task, 1) == "http://127.0.0.1:9002"
        assert state["launches"] == 2
    finally:
        finish.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await supervisor.stop()


@pytest.mark.asyncio
async def test_parallel_wake_and_cancelled_waiter_do_not_leak_requests(managed, monkeypatch):
    supervisor, state = managed
    await supervisor.start()
    await asyncio.wait_for(state["closed"].wait(), 1)
    starting, finish = asyncio.Event(), asyncio.Event()
    launch = supervisor._launch

    async def paused_launch():
        starting.set()
        await finish.wait()
        await launch()
    monkeypatch.setattr(supervisor, "_launch", paused_launch)
    async def request():
        async with supervisor.activity():
            return supervisor.cdp_url
    tasks = [asyncio.create_task(request()) for _ in range(4)]
    try:
        await starting.wait()
        tasks[-1].cancel()
        finish.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert results[:3] == ["http://127.0.0.1:9002"] * 3
        assert isinstance(results[3], asyncio.CancelledError)
        assert state["launches"] == 2
        assert supervisor._active_requests == 0
    finally:
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await supervisor.stop()
    with pytest.raises(RuntimeError, match="stopp"):
        async with supervisor.activity():
            pass
    assert state["launches"] == 2


@pytest.mark.asyncio
async def test_cancelled_launch_is_cleaned_before_next_request(managed, monkeypatch):
    supervisor, state = managed
    await supervisor.start()
    await asyncio.wait_for(state["closed"].wait(), 1)
    started = asyncio.Event()
    launch = supervisor._launch
    async def interrupted_launch():
        await launch()
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(supervisor, "_launch", interrupted_launch)
    async def request():
        async with supervisor.activity():
            pass
    task = asyncio.create_task(request())
    try:
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not state["running"]
        assert supervisor._active_requests == 0
        monkeypatch.setattr(supervisor, "_launch", launch)
        async with supervisor.activity():
            assert state["running"]
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args,kwargs", [
    ("inspect_conversation", ("12345678-1234-1234-1234-123456789abc",), {"project_id": "g-p-" + "a" * 32}),
    ("inspect_project", ("g-p-" + "a" * 32,), {}),
    ("create_project", ("Example", "project_only"), {}),
    ("resolve_chat_marker", ("WATCHER-CHAT-" + "a" * 32,), {}),
    ("read_conversation", ("example",), {}),
    ("delete_conversation", ("12345678-1234-1234-1234-123456789abc",), {}),
    ("verify_conversation_deleted", ("12345678-1234-1234-1234-123456789abc",), {}),
    ("generate", (), {"session_id": "s", "conversation_id": None, "prompt": "hi", "request_id": "r"}),
])
async def test_all_browser_operations_hold_activity_until_error(method, args, kwargs):
    class Lease:
        active = 0
        @asynccontextmanager
        async def activity(self):
            self.active += 1
            try:
                yield
            finally:
                self.active -= 1
    lease = Lease()
    transport = BrowserTransport("http://127.0.0.1:9000", browser_supervisor=lease)
    class ExpectedError(Exception):
        pass
    async def ensure():
        assert lease.active == 1
        raise ExpectedError()
    transport._ensure = ensure
    with pytest.raises(ExpectedError):
        result = getattr(transport, method)(*args, **kwargs)
        if method == "generate":
            await anext(result)
        else:
            await result
    assert lease.active == 0


@pytest.mark.asyncio
async def test_idle_readiness_is_passive_and_explicit_wake_waits(managed, tmp_path):
    supervisor, state = managed
    await supervisor.start()
    await asyncio.wait_for(state["closed"].wait(), 1)
    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, MockTransport())
    await engine.start()
    client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"),
                                            browser_supervisor=supervisor)))
    await client.start_server()
    try:
        for _ in range(3):
            response = await client.get("/ready")
            body = await response.json()
            assert response.status == 503
            assert body["reason"] == "browser_idle"
            assert body["chatgpt_authenticated"] is None
            assert body["ready"] is False
        assert state["launches"] == 1
        response = await client.post("/v1/browser/wake")
        assert response.status == 200
        assert (await response.json())["browser"]["state"] == "running"
        assert state["launches"] == 2
    finally:
        await client.close()
        await engine.stop()
        await supervisor.stop()
        store.close()


@pytest.mark.asyncio
async def test_wake_failure_is_503_with_reserved_profile(managed, monkeypatch, tmp_path):
    supervisor, state = managed
    await supervisor.start()
    await asyncio.wait_for(state["closed"].wait(), 1)
    async def failed_launch():
        raise TimeoutError("Chrome failed to start")
    monkeypatch.setattr(supervisor, "_launch", failed_launch)
    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, MockTransport())
    client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"),
                                            browser_supervisor=supervisor)))
    await client.start_server()
    try:
        response = await client.post("/v1/browser/wake")
        assert response.status == 503
        body = await response.json()
        assert body["error"]["code"] == "browser_unavailable"
        assert body["browser"]["owned"] is True
        assert "TimeoutError" in body["browser"]["last_error"]
    finally:
        await client.close()
        await supervisor.stop()
        store.close()


@pytest.mark.asyncio
async def test_transport_discards_still_connected_old_cdp_endpoint(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from chatbridge import browser_transport

    supervisor = SimpleNamespace(cdp_url="http://127.0.0.1:9002")
    transport = BrowserTransport("http://127.0.0.1:9001", browser_supervisor=supervisor)
    old_browser = Mock()
    old_browser.is_connected.return_value = True
    transport._browser = old_browser
    transport._connected_cdp_url = transport.cdp_url
    old_driver = SimpleNamespace(stop=AsyncMock())
    transport._pw = old_driver
    new_browser = Mock()
    connect = AsyncMock(return_value=new_browser)
    driver = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    monkeypatch.setattr(browser_transport, "async_playwright",
                        lambda: SimpleNamespace(start=AsyncMock(return_value=driver)))
    assert await transport._ensure() is new_browser
    old_driver.stop.assert_awaited_once()
    connect.assert_awaited_once_with(supervisor.cdp_url, timeout=15000)
    assert transport._connected_cdp_url == supervisor.cdp_url


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_cancelled_browser_operation_releases_lease(managed, stream):
    supervisor, _ = managed
    await supervisor.start()
    transport = BrowserTransport(supervisor.cdp_url, browser_supervisor=supervisor)
    entered = asyncio.Event()
    async def blocked_ensure():
        entered.set()
        await asyncio.Event().wait()
    transport._ensure = blocked_ensure
    if stream:
        source = transport.generate(session_id="s", conversation_id=None, prompt="hi", request_id="r")
        task = asyncio.create_task(anext(source))
    else:
        task = asyncio.create_task(transport.read_conversation("example"))
    try:
        await entered.wait()
        assert supervisor._active_requests == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert supervisor._active_requests == 0
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_stop_during_wake_prevents_late_running_browser(managed, monkeypatch):
    supervisor, state = managed
    await supervisor.start()
    await asyncio.wait_for(state["closed"].wait(), 1)
    launched, finish = asyncio.Event(), asyncio.Event()
    launch = supervisor._launch
    async def delayed_launch():
        await launch()
        launched.set()
        await finish.wait()
    monkeypatch.setattr(supervisor, "_launch", delayed_launch)
    async def request():
        async with supervisor.activity():
            pytest.fail("shutdown must reject pending startup")
    request_task = asyncio.create_task(request())
    await launched.wait()
    stop_task = asyncio.create_task(supervisor.stop())
    await asyncio.sleep(0)
    finish.set()
    await stop_task
    with pytest.raises(RuntimeError, match="stopp"):
        await request_task
    assert supervisor.status()["state"] == "stopped"
    assert not state["running"]
    assert supervisor._lockfile is None


@pytest.mark.asyncio
async def test_unverified_live_process_is_not_killed_or_forgotten(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from chatbridge import browser_supervisor

    supervisor = ChromeSupervisor(tmp_path / "profile", tmp_path / "chrome")
    proc = Mock(pid=123)
    proc.poll.return_value = None
    supervisor.process = proc
    supervisor.cdp_url = "http://127.0.0.1:9001"
    monkeypatch.setattr(supervisor, "_identity", lambda pid: None)
    kill = Mock()
    monkeypatch.setattr(browser_supervisor.os, "killpg", kill)
    with pytest.raises(RuntimeError, match="unverified"):
        await supervisor._terminate_owned()
    kill.assert_not_called()
    assert supervisor.process is proc
    assert supervisor.cdp_url == "http://127.0.0.1:9001"


@pytest.mark.asyncio
async def test_process_still_alive_after_sigkill_is_not_forgotten(tmp_path, monkeypatch):
    import signal
    from unittest.mock import AsyncMock, Mock

    from chatbridge import browser_supervisor

    supervisor = ChromeSupervisor(tmp_path / "profile", tmp_path / "chrome")
    proc = Mock(pid=123)
    proc.poll.return_value = None
    supervisor.process = proc
    supervisor.cdp_url = "http://127.0.0.1:9001"
    monkeypatch.setattr(supervisor, "_identity", lambda pid: {"pid": pid})
    kill = Mock()
    monkeypatch.setattr(browser_supervisor.os, "killpg", kill)
    monkeypatch.setattr(browser_supervisor.asyncio, "sleep", AsyncMock())
    with pytest.raises(RuntimeError, match="did not exit"):
        await supervisor._terminate_owned()
    assert [call.args[1] for call in kill.call_args_list] == [signal.SIGTERM, signal.SIGKILL]
    assert supervisor.process is proc
    assert supervisor.cdp_url == "http://127.0.0.1:9001"


@pytest.mark.asyncio
async def test_cancelled_service_start_releases_owned_process_and_profile(managed, monkeypatch):
    supervisor, state = managed
    entered = asyncio.Event()
    launch = supervisor._launch
    async def blocked_start():
        await launch()
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(supervisor, "_launch", blocked_start)
    task = asyncio.create_task(supervisor.start())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not state["running"]
    assert supervisor._lockfile is None
    assert supervisor._watchdog is None


@pytest.mark.asyncio
async def test_wake_uses_api_auth_and_openapi_describes_result(managed, tmp_path):
    supervisor, state = managed
    await supervisor.start()
    await asyncio.wait_for(state["closed"].wait(), 1)
    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, MockTransport())
    client = TestClient(TestServer(make_app(store, engine,
        Settings(transport="browser", api_key="test-only-key"), browser_supervisor=supervisor)))
    await client.start_server()
    try:
        response = await client.post("/v1/browser/wake")
        assert response.status == 401
        assert state["launches"] == 1
        headers = {"Authorization": "Bearer test-only-key"}
        response = await client.post("/v1/browser/wake", headers={**headers, "Origin": "https://foreign.invalid"})
        assert response.status == 403
        assert state["launches"] == 1
        response = await client.get("/v1/openapi.json", headers=headers)
        schema = await response.json()
        operation = schema["paths"]["/v1/browser/wake"]["post"]
        assert {"200", "409", "503"} <= operation["responses"].keys()
        assert {"OptionalBearer": []} in operation["security"]
    finally:
        await client.close()
        await supervisor.stop()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["mock", "browser"])
async def test_wake_rejects_transports_without_managed_supervisor(tmp_path, mode):
    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, MockTransport())
    client = TestClient(TestServer(make_app(store, engine, Settings(transport=mode))))
    await client.start_server()
    try:
        response = await client.post("/v1/browser/wake")
        assert response.status == 409
    finally:
        await client.close()
        store.close()


@pytest.mark.asyncio
async def test_closing_generate_after_first_event_closes_page_and_releases_lease(managed):
    from contextlib import aclosing
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    supervisor, _ = managed
    await supervisor.start()
    transport = BrowserTransport(supervisor.cdp_url, browser_supervisor=supervisor)
    editor = Mock()
    editor.first = editor
    editor.wait_for = AsyncMock()
    editor.press = AsyncMock()
    page = Mock()
    page.locator.return_value = editor
    page.is_closed.return_value = False
    page.add_init_script = AsyncMock()
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.evaluate = AsyncMock(side_effect=[{}, {"requestId": "provider-message"},
                                          {"requestId": "provider-message"}])
    transport._ensure = AsyncMock(return_value=SimpleNamespace(contexts=[object()]))
    transport._new_page = AsyncMock(return_value=page)
    transport._configure_reasoning_effort = AsyncMock()
    transport._fill_composer = AsyncMock()
    try:
        async with aclosing(transport.generate(session_id="s", conversation_id=None,
                                               prompt="hi", request_id="r")) as stream:
            event = await anext(stream)
            assert event.type == "submitted"
            assert supervisor._active_requests == 1
            assert page in transport._owned_pages
        page.close.assert_awaited_once()
        assert not transport._owned_pages
        assert supervisor._active_requests == 0
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_readiness_probe_does_not_delay_idle_and_reports_race(managed, tmp_path):
    from unittest.mock import AsyncMock, Mock

    supervisor, state = managed
    await supervisor.start()
    timestamp = supervisor._last_activity
    page = Mock()
    page.close = AsyncMock()
    editor = Mock()
    editor.first = editor
    editor.wait_for = AsyncMock()
    page.locator.return_value = editor
    async def slow_navigation(*args, **kwargs):
        await state["closed"].wait()
    page.goto = slow_navigation
    transport = MockTransport()
    transport._ensure = AsyncMock(return_value=object())
    transport._new_page = AsyncMock(return_value=page)
    transport._observe_provider_response = Mock()
    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, transport)
    client = TestClient(TestServer(make_app(store, engine, Settings(transport="browser"),
                                            browser_supervisor=supervisor)))
    await client.start_server()
    try:
        response = await asyncio.wait_for(client.get("/ready"), 1)
        assert response.status == 503
        assert (await response.json())["reason"] == "browser_idle"
        assert supervisor._last_activity == timestamp
        assert supervisor._active_requests == 0
        assert state["launches"] == 1
    finally:
        await client.close()
        await supervisor.stop()
        store.close()


@pytest.mark.asyncio
async def test_installed_wheel_can_serve_its_packaged_openapi(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from chatbridge import server

    schema = json.loads((Path(__file__).resolve().parents[1] / "docs/openapi.json").read_text())
    package = tmp_path / "site-packages/chatbridge"
    package.mkdir(parents=True)
    (package / "openapi.json").write_text(json.dumps(schema))
    monkeypatch.setattr(server, "__file__", str(package / "server.py"))
    store = Store(tmp_path / "db.sqlite")
    engine = GatewayEngine(store, MockTransport())
    client = TestClient(TestServer(make_app(store, engine, Settings(transport="mock"))))
    await client.start_server()
    try:
        response = await client.get("/v1/openapi.json")
        assert response.status == 200
        assert "/v1/browser/wake" in (await response.json())["paths"]
    finally:
        await client.close()
        store.close()
