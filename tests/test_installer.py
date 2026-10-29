"""Offline Mac installer tests; never register a real LaunchAgent or start Chrome."""

import importlib.util
import json
import plistlib
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "installer.py"
SPEC = importlib.util.spec_from_file_location("standalone_installer", MODULE)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def args(tmp, *, command="install", **kwargs):
    default = dict(
        label="net.chatbridge.test-unit",
        prefix=str(tmp / "app"),
        state_dir=str(tmp / "data"),
        host=None,
        port=None,
        max_active=None,
        reasoning_effort=None,
        chrome_path=str(tmp / "fake-chrome"),
        api_key=None,
        no_start=True,
        dry_run=False,
        purge_data=False,
        yes=False,
        remove_app=False,
    )
    default.update(kwargs)
    return SimpleNamespace(command=command, **default)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    chrome = tmp_path / "fake-chrome"
    chrome.write_text("fake")
    release = tmp_path / "app" / "releases" / "test-release"
    (release / ".venv/bin").mkdir(parents=True)
    (release / ".venv/bin/python").write_text("fake")
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(installer, "agent_directory", lambda: agents)
    monkeypatch.setattr(installer, "require_os", lambda: None)
    monkeypatch.setattr(installer, "require_uv", lambda: "/fake/uv")
    monkeypatch.setattr(installer, "build_release", lambda uv, prefix: (release, False))
    monkeypatch.setattr(installer, "is_loaded", lambda label: False)
    monkeypatch.setattr(installer, "busy", lambda host, port: False)
    monkeypatch.delenv("CHATBRIDGE_API_KEY", raising=False)
    return agents


def test_install_preserves_private_state_and_generates_launchd(tmp_path, isolated):
    setting = args(tmp_path, port=18787, max_active=10)
    installer.do_install(setting)
    plist = isolated / (setting.label + ".plist")
    config = plistlib.loads(plist.read_bytes())
    assert config["RunAtLoad"] is True
    assert config["KeepAlive"] is True
    assert config["ThrottleInterval"] == 15
    assert config["ChatBridgeInstaller"] == "1"
    assert config["EnvironmentVariables"]["CHATBRIDGE_BROWSER_MODE"] == "managed"
    assert config["EnvironmentVariables"]["CHATBRIDGE_PORT"] == "18787"
    assert config["EnvironmentVariables"]["CHATBRIDGE_REASONING_EFFORT"] == "high"
    assert "CHATBRIDGE_CDP" not in config["EnvironmentVariables"]
    assert "CHATBRIDGE_API_KEY" not in config["EnvironmentVariables"]
    assert config["WorkingDirectory"].endswith("test-release")
    assert stat.S_IMODE(plist.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o700
    assert (tmp_path / "data/.chatbridge-installed").read_text() == "1\n"


def test_upgrade_inherits_custom_state_port_and_reasoning(tmp_path, isolated):
    first = args(tmp_path, port=18844, max_active=4, reasoning_effort="medium")
    installer.do_install(first)
    second = args(tmp_path, command="upgrade", prefix=None, state_dir=None, chrome_path=None)
    installer.do_install(second, update=True)
    plist = plistlib.loads((isolated / (first.label + ".plist")).read_bytes())
    settings = plist["EnvironmentVariables"]
    assert settings["CHATBRIDGE_PORT"] == "18844"
    assert settings["CHATBRIDGE_MAX_ACTIVE"] == "4"
    assert settings["CHATBRIDGE_REASONING_EFFORT"] == "medium"
    assert settings["CHATBRIDGE_DB"] == str((tmp_path / "data/state.db").resolve())
    assert len(list((tmp_path / "data/installer-backups").glob("*.plist"))) == 1


def test_refuses_to_overwrite_existing_unmanaged_launchagent(tmp_path, isolated):
    setting = args(tmp_path)
    plist = isolated / (setting.label + ".plist")
    original = plistlib.dumps({"Label": setting.label, "RunAtLoad": True})
    plist.write_bytes(original)
    with pytest.raises(installer.InstallationError, match="LaunchAgent exists"):
        installer.do_install(setting)
    with pytest.raises(installer.InstallationError, match="not created"):
        installer.do_install(args(tmp_path, command="upgrade"), update=True)
    assert plist.read_bytes() == original


def test_upgrade_rolls_back_plist_on_failure(tmp_path, monkeypatch, isolated):
    setting = args(tmp_path)
    installer.do_install(setting)
    plist_path = isolated / (setting.label + ".plist")
    old = plist_path.read_bytes()
    monkeypatch.setattr(installer, "is_loaded", lambda label: True)
    stops = []
    monkeypatch.setattr(installer, "stop", lambda label: stops.append(label))
    monkeypatch.setattr(
        installer,
        "start",
        lambda label, plist: (_ for _ in ()).throw(RuntimeError("launchd test failure")),
    )
    upgrade = args(
        tmp_path, command="upgrade", prefix=None, state_dir=None, chrome_path=None, no_start=False
    )
    with pytest.raises(RuntimeError, match="launchd test failure"):
        installer.do_install(upgrade, update=True)
    assert plist_path.read_bytes() == old
    assert len(stops) >= 2


def test_uninstall_leaves_browser_profile_and_db_intact(tmp_path, isolated):
    setting = args(tmp_path)
    installer.do_install(setting)
    state = tmp_path / "data"
    (state / "chrome-profile").mkdir()
    (state / "state.db").write_bytes(b"saved database")
    installer.do_uninstall(setting)
    assert not (isolated / (setting.label + ".plist")).exists()
    assert (state / "state.db").read_bytes() == b"saved database"
    assert (state / "chrome-profile").is_dir()


def test_purge_requires_explicit_yes_and_owned_state(tmp_path, isolated):
    setting = args(tmp_path)
    installer.do_install(setting)
    setting.purge_data = True
    with pytest.raises(installer.InstallationError, match="requires --yes"):
        installer.do_uninstall(setting)
    assert (isolated / (setting.label + ".plist")).is_file()
    (tmp_path / "data/.chatbridge-installed").unlink()
    setting.yes = True
    with pytest.raises(installer.InstallationError, match="unowned"):
        installer.do_uninstall(setting)
    assert (isolated / (setting.label + ".plist")).is_file()


def test_no_start_refuses_to_change_a_loaded_service(tmp_path, monkeypatch, isolated):
    setting = args(tmp_path)
    installer.do_install(setting)
    monkeypatch.setattr(installer, "is_loaded", lambda label: True)
    with pytest.raises(installer.InstallationError, match="cannot replace"):
        installer.do_install(args(tmp_path, command="upgrade"), update=True)


def test_non_loopback_requires_authentication(tmp_path, isolated):
    with pytest.raises(installer.InstallationError, match="requires --api-key"):
        installer.do_install(args(tmp_path, host="0.0.0.0"))


def test_status_never_echoes_api_key(tmp_path, monkeypatch, capsys, isolated):
    setting = args(tmp_path, port=18881, api_key="TOP_PRIVATE_TEST_KEY")
    installer.do_install(setting)
    monkeypatch.setattr(installer, "probe", lambda *a, **kw: True)
    monkeypatch.setattr(
        installer, "fetch_json", lambda *a, **kw: {"ready": True, "chatgpt_authenticated": True}
    )
    installer.do_status(setting)
    printed = capsys.readouterr().out
    parsed = json.loads(printed[printed.index("{") :])
    assert parsed["installed"] is True
    assert parsed["http_alive"] is True
    assert parsed["chatgpt_ready"] is True
    assert "TOP_PRIVATE_TEST_KEY" not in printed


def test_release_tag_is_deterministic_and_covers_docs(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src/main.py").write_text("hello")
    for name in ("INSTALL.md", "API_REFERENCE.md", "ATTACHMENTS.md", "IMAGES.md"):
        (tmp_path / "docs" / name).write_text("docs v1")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0a1"\n')
    for name in ("uv.lock", "LICENSE", "SECURITY.md", "README.md", "README.ru.md"):
        (tmp_path / name).write_text(name)
    monkeypatch.setattr(installer, "SOURCE", tmp_path)
    monkeypatch.setattr(installer, "MANIFEST", ("src/main.py", "docs/INSTALL.md", "pyproject.toml"))
    first = installer.version_tag()
    assert installer.version_tag() == first
    (tmp_path / "docs/INSTALL.md").write_text("docs v2")
    assert installer.version_tag() != first


def test_same_data_directory_cannot_be_claimed_by_two_services(tmp_path, isolated):
    original = args(tmp_path)
    installer.do_install(original)
    second = args(tmp_path, label="net.chatbridge.second-instance")
    with pytest.raises(installer.InstallationError, match="already assigned"):
        installer.do_install(second)
    assert not (isolated / (second.label + ".plist")).exists()


def test_shared_app_directory_cannot_be_deleted_while_other_label_uses_it(tmp_path, isolated):
    original = args(tmp_path)
    installer.do_install(original)
    second = args(
        tmp_path, label="net.chatbridge.second-instance", state_dir=str(tmp_path / "other")
    )
    installer.do_install(second)
    original.remove_app = True
    with pytest.raises(installer.InstallationError, match="used by another"):
        installer.do_uninstall(original)
    assert (isolated / (original.label + ".plist")).is_file()


def test_malformed_unrelated_plist_does_not_block_install(tmp_path, isolated):
    (isolated / "broken-other-agent.plist").write_text("<plist><dict>broken</plist>")
    installer.do_install(args(tmp_path, port=18770))
    assert (isolated / "net.chatbridge.test-unit.plist").is_file()


def test_release_tag_ignores_files_omitted_from_release(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nversion = "0.2.0b2"\n')
    (source / "src").mkdir()
    (source / "src/main.py").write_text("pass\n")
    monkeypatch.setattr(installer, "SOURCE", source)
    monkeypatch.setattr(installer, "MANIFEST", ("src/main.py", "pyproject.toml"))
    before = installer.version_tag()
    cache = source / "src/__pycache__"
    cache.mkdir()
    (cache / "main.cpython-313.pyc").write_bytes(b"generated cache")
    (source / "src/.DS_Store").write_bytes(b"metadata")
    (source / "src/.env").write_text("PRIVATE=do-not-copy")
    (source / "src/private_tool.py").write_text("raise RuntimeError()")
    assert installer.version_tag() == before
    target = tmp_path / "release"
    installer.release_files(target)
    assert not (target / "src/__pycache__").exists()
    assert not (target / "src/.DS_Store").exists()
    assert not (target / "src/.env").exists()
    assert not (target / "src/private_tool.py").exists()


def test_runtime_manifest_covers_all_package_modules():
    root = MODULE.parents[1]
    modules = {str(path.relative_to(root)) for path in (root / "src/chatbridge").glob("*.py")}
    assert modules == {name for name in installer.MANIFEST if name.startswith("src/")}
