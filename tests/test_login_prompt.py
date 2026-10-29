"""Manual login consent never opens a different or unready Chrome profile."""
import importlib.util
import io
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "login_installer", Path(__file__).resolve().parents[1] / "scripts/installer.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def login(tmp_path, monkeypatch):
    chrome = tmp_path / "Chrome's App" / "Chrome"
    profile = tmp_path / "State $literal;" / "chrome-profile"
    status = {"browser": {"mode": "managed", "state": "running",
                          "profile": str(profile), "headless": False}}
    calls = []
    monkeypatch.setattr(installer.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(installer, "fetch_json", lambda *a, **kw: status)
    monkeypatch.setattr(installer.time, "sleep", lambda _: None)
    monkeypatch.setattr(installer.subprocess, "Popen", lambda argv, **kw: (
        calls.append((argv, kw)) or SimpleNamespace(returncode=0)
    ))
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    return chrome, profile, status, calls


def invoke(login):
    chrome, profile, _, _ = login
    return installer.offer_login(chrome, profile, "127.0.0.1", 8787, "PRIVATE_KEY")


def test_consent_opens_exact_profile_without_shell(login):
    invoke(login)
    chrome, profile, _, calls = login
    assert calls == [([str(chrome), "--user-data-dir=" + str(profile),
                       "--new-window", "https://chatgpt.com/"],
                      {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                       "stderr": subprocess.DEVNULL, "start_new_session": True})]


@pytest.mark.parametrize("answer", ["", "n", "no", "anything"])
def test_decline_does_not_open(login, monkeypatch, answer):
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    invoke(login)
    assert not login[3]


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_cancel_is_optional(login, monkeypatch, error):
    def cancelled(prompt):
        raise error
    monkeypatch.setattr("builtins.input", cancelled)
    invoke(login)
    assert not login[3]


def test_non_tty_neither_polls_nor_prompts(login, monkeypatch):
    monkeypatch.setattr(installer.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(installer, "fetch_json", lambda *a, **kw: pytest.fail("must not poll"))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("must not prompt"))
    invoke(login)
    assert not login[3]


@pytest.mark.parametrize("change", [
    {"mode": "external"}, {"state": "starting"}, {"headless": True},
    {"profile": "/different/profile"}, {"profile": None}, {"headless": None},
])
def test_unready_or_wrong_browser_never_prompts(login, monkeypatch, change):
    login[2]["browser"].update(change)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unsafe prompt"))
    invoke(login)
    assert not login[3]


def test_waits_for_managed_browser_and_rechecks_after_consent(login, monkeypatch):
    seen = []
    def fetch(*args, **kwargs):
        seen.append((args, kwargs))
        return {"browser": {"state": "starting"}} if len(seen) == 1 else login[2]
    monkeypatch.setattr(installer, "fetch_json", fetch)
    invoke(login)
    assert len(seen) == 3
    assert all(call[0][2] == "/v1/system/status" for call in seen)
    assert all(call[1]["api_key"] == "PRIVATE_KEY" for call in seen)
    assert login[3]


def test_browser_change_during_prompt_does_not_open(login, monkeypatch):
    def answer(prompt):
        login[2]["browser"]["state"] = "unavailable"
        return "y"
    monkeypatch.setattr("builtins.input", answer)
    invoke(login)
    assert not login[3]


def test_open_failure_does_not_fail_installation_or_print_secrets(login, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise OSError("failure")
    monkeypatch.setattr(installer.subprocess, "Popen", failed)
    invoke(login)
    output = capsys.readouterr().out
    assert "installed" in output.lower()
    assert "PRIVATE_KEY" not in output


def test_post_start_waits_for_browser_before_slow_authenticated_probe(login, monkeypatch, capsys):
    requests = []
    def fetch(host, port, path, **kwargs):
        requests.append((path, kwargs))
        if len(requests) == 1:
            return {"browser": {"mode": "managed", "state": "starting"}}
        if path == "/v1/system/status":
            return login[2]
        assert kwargs["timeout"] >= 35
        return {"ready": True, "chatgpt_authenticated": True}
    monkeypatch.setattr(installer, "fetch_json", fetch)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("saved login must not prompt"))
    installer.check_started_login(login[0], login[1], "127.0.0.1", 8787, "PRIVATE_KEY")
    assert [path for path, _ in requests] == ["/v1/system/status", "/v1/system/status", "/ready"]
    assert "authenticated" in capsys.readouterr().out
    assert not login[3]


@pytest.fixture
def startup_clock(login, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(installer.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(installer.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


@pytest.mark.parametrize("startup_seconds", [0, 30])
def test_post_start_wait_covers_slow_chrome_without_delaying_early_ready(
    login, monkeypatch, startup_clock, startup_seconds
):
    readiness_times = []
    def fetch(host, port, path, **kwargs):
        if path == "/v1/system/status":
            return (login[2] if startup_clock[0] >= startup_seconds else
                    {"browser": {"mode": "managed", "state": "starting"}})
        readiness_times.append(startup_clock[0])
        return {"ready": True, "chatgpt_authenticated": True}
    monkeypatch.setattr(installer, "fetch_json", fetch)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("saved login must not prompt"))
    installer.check_started_login(login[0], login[1], "127.0.0.1", 8787, "PRIVATE_KEY")
    assert readiness_times == [startup_seconds]


def test_post_start_status_timeouts_share_a_40_second_deadline(login, monkeypatch, startup_clock):
    def fetch(host, port, path, **kwargs):
        assert path == "/v1/system/status"
        assert 0 < kwargs["timeout"] <= min(1, 40 - startup_clock[0])
        startup_clock[0] += kwargs["timeout"]
        return None
    monkeypatch.setattr(installer, "fetch_json", fetch)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unsafe prompt"))
    installer.check_started_login(login[0], login[1], "127.0.0.1", 8787, "PRIVATE_KEY")
    assert startup_clock[0] == 40


@pytest.mark.parametrize("ready", [None, {"ready": False, "chatgpt_authenticated": False,
                                          "reason": "auth_required_or_browser_unavailable"}])
def test_uncertain_readiness_offers_manual_check_without_claiming_logout(login, monkeypatch, capsys, ready):
    monkeypatch.setattr(installer, "fetch_json", lambda host, port, path, **kw:
                        login[2] if path == "/v1/system/status" else ready)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    installer.check_started_login(login[0], login[1], "127.0.0.1", 8787, "PRIVATE_KEY")
    output = capsys.readouterr().out
    assert "could not be verified" in output
    assert "sign in" not in output.lower()
    assert "inspect" in prompts[0]
    assert "PRIVATE_KEY" not in output


def test_http_503_json_is_available_for_diagnosis(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError("http://localhost/ready", 503, "unavailable", {},
                                     io.BytesIO(b'{"ready":false,"reason":"browser_unavailable"}'))
    monkeypatch.setattr(installer.urllib.request, "urlopen", unavailable)
    assert installer.fetch_json("127.0.0.1", 8787, "/ready")["reason"] == "browser_unavailable"


def test_cached_failure_is_retried_after_expiry_before_offering_window(login, monkeypatch):
    ready_calls = []
    sleeps = []
    def fetch(host, port, path, **kwargs):
        if path == "/v1/system/status":
            return login[2]
        ready_calls.append(kwargs)
        return ({"ready": False, "chatgpt_authenticated": False, "cached": True}
                if len(ready_calls) == 1 else {"ready": True, "chatgpt_authenticated": True})
    monkeypatch.setattr(installer, "fetch_json", fetch)
    monkeypatch.setattr(installer.time, "sleep", sleeps.append)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("saved login must not prompt"))
    installer.check_started_login(login[0], login[1], "127.0.0.1", 8787, "PRIVATE_KEY")
    assert len(ready_calls) == 2
    assert sleeps == [20]


@pytest.mark.parametrize("status", [None, {"browser": {"mode": "managed", "state": "starting"}},
                                    {"transport": "mock", "browser": {"mode": "mock"}},
                                    {"browser": {"mode": "external", "state": "running"}}])
def test_post_start_unready_or_mock_never_probes_auth_or_prompts(login, monkeypatch, startup_clock, status):
    requests = []
    def fetch(host, port, path, **kwargs):
        requests.append(path)
        assert path == "/v1/system/status"
        return status
    monkeypatch.setattr(installer, "fetch_json", fetch)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unsafe prompt"))
    installer.check_started_login(login[0], login[1], "127.0.0.1", 8787, "PRIVATE_KEY")
    assert len(requests) <= 80
    assert startup_clock[0] <= 40
    assert not login[3]



@pytest.mark.parametrize("status", [None, [], {"browser": None}, {"browser": []}])
def test_bad_status_never_prompts(login, monkeypatch, status):
    monkeypatch.setattr(installer, "fetch_json", lambda *a, **kw: status)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unsafe prompt"))
    invoke(login)
    assert not login[3]


@pytest.mark.parametrize("no_start,dry_run,ready,offered", [
    (False, False, False, True),
    (False, False, True, False),
    (True, False, False, False),
    (False, True, False, False),
])
def test_install_offers_login_only_after_started_and_not_ready(
    tmp_path, monkeypatch, no_start, dry_run, ready, offered
):
    chrome = tmp_path / "chrome"
    chrome.touch()
    release = tmp_path / "release"
    release.mkdir()
    (tmp_path / "app").mkdir()
    setting = SimpleNamespace(
        label="net.chatbridge.prompt-test", prefix=str(tmp_path / "app"),
        state_dir=str(tmp_path / "state"), chrome_path=str(chrome), host=None, port=None,
        max_active=None, reasoning_effort=None, api_key=None, no_start=no_start,
        dry_run=dry_run,
    )
    monkeypatch.setattr(installer, "require_os", lambda: None)
    monkeypatch.setattr(installer, "agent_directory", lambda: tmp_path / "agents")
    monkeypatch.setattr(installer, "is_loaded", lambda _: False)
    monkeypatch.setattr(installer, "busy", lambda *a: False)
    monkeypatch.setattr(installer, "find_uv", lambda: "/fake/uv")
    monkeypatch.setattr(installer, "require_uv", lambda: "/fake/uv")
    monkeypatch.setattr(installer, "build_release", lambda *a: (release, False))
    monkeypatch.setattr(installer, "start", lambda *a: None)
    monkeypatch.setattr(installer, "probe", lambda host, port, path="/health", **kw:
                        path != "/ready" or ready)
    monkeypatch.setattr(installer, "fetch_json", lambda host, port, path, **kw:
                        {"browser": {"mode": "managed", "state": "running"}}
                        if path == "/v1/system/status" else
                        {"ready": ready, "chatgpt_authenticated": ready})
    calls = []
    monkeypatch.setattr(installer, "offer_login", lambda *a: calls.append(a))
    installer.do_install(setting)
    assert bool(calls) is offered
