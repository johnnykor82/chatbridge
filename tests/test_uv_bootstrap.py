"""Offline regression tests for first-run uv bootstrap.

All installation commands and network access are mocked. These tests must not
install Homebrew, uv, or anything else on the development computer.
"""
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts/installer.py"
SPEC = importlib.util.spec_from_file_location("chatbridge_uv_bootstrap", FILE)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def interactive(monkeypatch, answer="y"):
    monkeypatch.setattr(installer.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: answer)


def missing_uv(monkeypatch):
    available = iter([None, "/home/example/.local/bin/uv"])
    monkeypatch.setattr(installer, "find_uv", lambda: next(available))


@pytest.mark.parametrize("brew", ["/usr/local/bin/brew", "/opt/homebrew/bin/brew", None])
def test_missing_uv_uses_standalone_even_when_homebrew_exists(monkeypatch, capsys, brew):
    interactive(monkeypatch)
    missing_uv(monkeypatch)
    monkeypatch.setattr(installer.shutil, "which", lambda name: brew if name == "brew" else None)
    calls = []
    monkeypatch.setattr(installer, "_install_uv_standalone", lambda: calls.append("standalone"))
    monkeypatch.setattr(installer, "run", lambda argv: pytest.fail("must not invoke Homebrew"))
    assert installer.require_uv() == "/home/example/.local/bin/uv"
    assert calls == ["standalone"]
    output = capsys.readouterr().out
    assert "official standalone installer" in output
    assert "Continuing ChatBridge installation" in output


def test_no_homebrew_offers_official_standalone_installer(monkeypatch, capsys):
    interactive(monkeypatch)
    missing_uv(monkeypatch)
    for key in ("UV_INSTALL_DIR", "XDG_BIN_HOME", "CARGO_HOME"):
        monkeypatch.setenv(key, "/custom/" + key.lower())
    requests = []
    monkeypatch.setattr(
        installer.urllib.request, "urlopen",
        lambda url, timeout: (requests.append(url) or io.BytesIO(b"#!/bin/sh\nexit 0\n"))
    )
    commands = []
    def simulate_run(argv, **kwargs):
        commands.append((argv, kwargs))
        assert argv[0] == "/bin/sh"
        assert Path(argv[1]).read_text().startswith("#!/bin/sh")
        assert kwargs["env"]["UV_NO_MODIFY_PATH"] == "1"
        assert kwargs["env"]["UV_INSTALL_DIR"] == str(Path.home() / ".local/bin")
    monkeypatch.setattr(installer.subprocess, "run", simulate_run)
    assert installer.require_uv() == "/home/example/.local/bin/uv"
    assert requests == ["https://astral.sh/uv/install.sh"]
    assert len(commands) == 1
    assert "official standalone installer" in capsys.readouterr().out
    assert installer.os.environ["UV_INSTALL_DIR"] == "/custom/uv_install_dir"


@pytest.mark.parametrize("answer", ["n", "", "no"])
def test_refusing_uv_install_does_not_run_commands(monkeypatch, answer):
    interactive(monkeypatch, answer)
    monkeypatch.setattr(installer, "find_uv", lambda: None)
    monkeypatch.setattr(installer, "run", lambda argv: pytest.fail("must not install"))
    monkeypatch.setattr(installer, "_install_uv_standalone", lambda: pytest.fail("must not install"))
    with pytest.raises(installer.InstallationError, match="declined.*https://docs.astral.sh"):
        installer.require_uv()


def test_noninteractive_never_installs_or_downloads(monkeypatch):
    monkeypatch.setattr(installer.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(installer, "find_uv", lambda: None)
    monkeypatch.setattr(installer, "_install_uv_standalone", lambda: pytest.fail("must not install"))
    with pytest.raises(installer.InstallationError, match="not interactive.*https://docs.astral.sh"):
        installer.require_uv()


def test_dry_run_without_uv_is_readonly(monkeypatch, tmp_path, capsys):
    chrome = tmp_path / "Chrome"
    chrome.write_text("fake")
    args = SimpleNamespace(
        prefix=str(tmp_path / "app"), state_dir=str(tmp_path / "state"),
        label="net.chatbridge.testunit", host=None, port=None, max_active=None,
        reasoning_effort=None, api_key=None, chrome_path=str(chrome),
        no_start=True, dry_run=True
    )
    monkeypatch.setattr(installer, "agent_directory", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(installer, "require_os", lambda: None)
    monkeypatch.setattr(installer, "find_uv", lambda: None)
    monkeypatch.setattr(installer, "require_uv", lambda: pytest.fail("must not install"))
    monkeypatch.setattr(installer, "is_loaded", lambda _: False)
    monkeypatch.setattr(installer, "busy", lambda *_: False)
    installer.do_install(args)
    assert "uv is missing" in capsys.readouterr().out
    assert not (tmp_path / "app").exists()
    assert not (tmp_path / "state").exists()


def test_uv_present_needs_no_install_prompt(monkeypatch):
    monkeypatch.setattr(installer, "find_uv", lambda: "/usr/local/bin/uv")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("must not prompt"))
    assert installer.require_uv() == "/usr/local/bin/uv"


def test_official_uv_installer_download_failure_is_actionable(monkeypatch):
    def offline(url, timeout):
        raise OSError("network disabled for test")
    monkeypatch.setattr(installer.urllib.request, "urlopen", offline)
    with pytest.raises(installer.InstallationError, match="Manual installation: https://docs.astral.sh"):
        installer._install_uv_standalone()


def test_official_uv_installer_rejects_oversized_script(monkeypatch):
    monkeypatch.setattr(
        installer.urllib.request, "urlopen",
        lambda url, timeout: io.BytesIO(b"x" * (512 * 1024 + 1))
    )
    with pytest.raises(installer.InstallationError, match="unexpectedly large"):
        installer._install_uv_standalone()


def test_bootstrap_does_not_fallback_to_homebrew_on_network_failure(monkeypatch):
    interactive(monkeypatch)
    monkeypatch.setattr(installer, "find_uv", lambda: None)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/local/bin/brew")
    monkeypatch.setattr(installer, "run", lambda argv: pytest.fail("must not invoke Homebrew"))
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: pytest.fail("must not execute"))

    def offline(url, timeout):
        raise OSError("offline")

    monkeypatch.setattr(installer.urllib.request, "urlopen", offline)
    with pytest.raises(installer.InstallationError, match="Manual installation: https://docs.astral.sh"):
        installer.require_uv()


def test_bootstrap_missing_executable_after_install_has_manual_guidance(monkeypatch):
    interactive(monkeypatch)
    monkeypatch.setattr(installer, "find_uv", lambda: None)
    monkeypatch.setattr(installer, "_install_uv_standalone", lambda: None)
    with pytest.raises(installer.InstallationError, match="executable was not found.*https://docs.astral.sh"):
        installer.require_uv()
