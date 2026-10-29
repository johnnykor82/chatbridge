"""Manual login consent never opens a different or unready Chrome profile."""
import importlib.util
import subprocess
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
    calls = []
    monkeypatch.setattr(installer, "offer_login", lambda *a: calls.append(a))
    installer.do_install(setting)
    assert bool(calls) is offered
