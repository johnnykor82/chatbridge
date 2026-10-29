"""Offline regression coverage for login instructions after browser idle."""

import json
import shlex

from test_installer import args, installer, isolated  # noqa: F401


def test_idle_doctor_offers_wake_before_saved_profile_login(
    tmp_path, monkeypatch, capsys, isolated  # noqa: F811
):
    setting = args(tmp_path, port=18844, state_dir=str(tmp_path / "Saved State"),
                   api_key="NEVER_PRINT_THIS_KEY")
    installer.do_install(setting)
    capsys.readouterr()
    calls = []
    monkeypatch.setattr(installer, "probe", lambda *a, **kw: True)
    monkeypatch.setattr(installer, "is_loaded", lambda label: True)

    def fetch(host, port, path, **kwargs):
        calls.append((host, port, path, kwargs.get("api_key")))
        if path == "/v1/system/status":
            return {"transport": "browser", "browser": {"mode": "managed", "state": "idle"}}
        return {"ready": False, "chatgpt_authenticated": None, "reason": "browser_idle"}

    monkeypatch.setattr(installer, "fetch_json", fetch)
    installer.do_status(args(tmp_path, command="doctor"), doctor=True)
    printed = capsys.readouterr().out
    result = json.loads(printed)
    assert "POST http://127.0.0.1:18844/v1/browser/wake" in result["next_action"]
    assert "browser.state=running" in result["next_action"]
    assert "Authorization" in result["next_action"]
    assert shlex.split(result["login_command"]) == [
        str(tmp_path / "fake-chrome"),
        "--user-data-dir=" + str(tmp_path / "Saved State/chrome-profile"),
        "--new-window", "https://chatgpt.com/",
    ]
    assert "NEVER_PRINT_THIS_KEY" not in printed
    assert calls == [
        ("127.0.0.1", 18844, "/v1/system/status", "NEVER_PRINT_THIS_KEY"),
        ("127.0.0.1", 18844, "/ready", "NEVER_PRINT_THIS_KEY"),
    ]


def test_later_login_instruction_wakes_custom_port_before_opening(
    tmp_path, monkeypatch, capsys, isolated  # noqa: F811
):
    monkeypatch.setattr(installer, "probe", lambda host, port, path="/health", **kw: path != "/ready")
    monkeypatch.setattr(installer, "start", lambda *a: None)
    monkeypatch.setattr(installer, "offer_login", lambda *a: None)
    installer.do_install(args(tmp_path, no_start=False, port=18844, api_key="NEVER_PRINT_THIS_KEY"))
    printed = capsys.readouterr().out
    wake = printed.index("POST http://127.0.0.1:18844/v1/browser/wake")
    wait = printed.index("browser.state running")
    opening = printed.index(installer.login_command(tmp_path / "fake-chrome", tmp_path / "data/chrome-profile"))
    assert wake < wait < opening
    assert "Authorization" in printed
    assert "NEVER_PRINT_THIS_KEY" not in printed
