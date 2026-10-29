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


def test_no_homebrew_offers_official_standalone_installer(monkeypatch, capsys, tmp_path):
    interactive(monkeypatch)
    missing_uv(monkeypatch)
    for key in ("UV_INSTALL_DIR", "XDG_BIN_HOME", "CARGO_HOME"):
        monkeypatch.setenv(key, "/custom/" + key.lower())
    requests = []
    monkeypatch.setattr(
        installer.urllib.request, "urlopen",
        lambda url, timeout: (requests.append(url) or io.BytesIO(b"#!/bin/sh\nexit 0\n"))
    )
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(installer, "uv_target", lambda: "aarch64-apple-darwin")
    commands = []
    def simulate_run(argv, **kwargs):
        commands.append((argv, kwargs))
        assert argv[:3] == ["/usr/bin/arch", "-arm64", "/bin/sh"]
        assert Path(argv[3]).read_text().startswith("#!/bin/sh")
        assert kwargs["env"]["UV_NO_MODIFY_PATH"] == "1"
        bin_dir = Path(kwargs["env"]["UV_INSTALL_DIR"])
        assert bin_dir.parent == Path(argv[3]).parent
        assert kwargs["env"]["UV_UNMANAGED_INSTALL"] == str(bin_dir)
        bin_dir.mkdir()
        (bin_dir / "uv").write_bytes(macho("arm64"))
    monkeypatch.setattr(installer.subprocess, "run", simulate_run)
    assert installer.require_uv() == "/home/example/.local/bin/uv"
    assert requests == ["https://astral.sh/uv/install.sh"]
    assert len(commands) == 1
    assert not Path(commands[0][0][3]).parent.exists()
    assert installer.uv_install_path("aarch64-apple-darwin").read_bytes() == macho("arm64")
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


def macho(arch):
    return b'\xcf\xfa\xed\xfe' + {'arm64': b'\x0c\x00\x00\x01', 'x86_64': b'\x07\x00\x00\x01'}[arch] + b'\0' * 24


def test_rosetta_detects_native_apple_silicon(monkeypatch):
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='1\n'))
    assert installer.uv_target() == 'aarch64-apple-darwin'


def test_intel_mac_uses_intel_target(monkeypatch):
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='0\n'))
    assert installer.uv_target() == 'x86_64-apple-darwin'


def test_find_uv_skips_intel_on_arm_and_finds_managed_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(installer, 'uv_target', lambda: 'aarch64-apple-darwin')
    intel = tmp_path / 'bin/uv'
    intel.parent.mkdir()
    intel.write_bytes(macho('x86_64'))
    intel.chmod(0o755)
    monkeypatch.setenv('PATH', str(intel.parent))
    managed = installer.uv_install_path('aarch64-apple-darwin')
    managed.parent.mkdir(parents=True)
    managed.write_bytes(macho('arm64'))
    managed.chmod(0o755)
    assert installer.find_uv() == str(managed)
    assert intel.read_bytes() == macho('x86_64')


@pytest.mark.parametrize('arch,target,expected', [
    ('arm64', 'aarch64-apple-darwin', True),
    ('x86_64', 'aarch64-apple-darwin', False),
    ('x86_64', 'x86_64-apple-darwin', True),
    ('arm64', 'x86_64-apple-darwin', False),
])
def test_uv_architecture_validation(tmp_path, arch, target, expected):
    uv = tmp_path / 'uv'
    uv.write_bytes(macho(arch))
    assert installer.uv_matches_target(uv, target) is expected


def uv_archive(target, content, *, member_name=None, kind=None):
    import tarfile
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        entry = tarfile.TarInfo(member_name or ('uv-' + target + '/uv'))
        entry.size = len(content)
        if kind:
            entry.type = kind
            entry.linkname = '/outside'
        archive.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


def setup_release(monkeypatch, tmp_path, archive, target='aarch64-apple-darwin'):
    import hashlib
    monkeypatch.setattr(installer.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(installer, 'uv_target', lambda: target)
    monkeypatch.setitem(installer.UV_RELEASE_SHA256, target, hashlib.sha256(archive).hexdigest())
    requests = []
    def download(url, timeout):
        requests.append(url)
        if url == installer.UV_INSTALL_URL:
            raise OSError('astral unavailable')
        return io.BytesIO(archive)
    monkeypatch.setattr(installer.urllib.request, 'urlopen', download)
    return requests


@pytest.mark.parametrize('arch,target', [('arm64', 'aarch64-apple-darwin'), ('x86_64', 'x86_64-apple-darwin')])
def test_astral_download_failure_uses_pinned_verified_github_binary(monkeypatch, tmp_path, arch, target):
    archive = uv_archive(target, macho(arch))
    requests = setup_release(monkeypatch, tmp_path, archive, target)
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: pytest.fail('must not execute installer or Homebrew'))
    installer._install_uv_standalone()
    installed = installer.uv_install_path(target)
    assert installed.read_bytes() == macho(arch)
    assert installed.stat().st_mode & 0o111
    assert requests == [installer.UV_INSTALL_URL, f'https://github.com/astral-sh/uv/releases/download/{installer.UV_RELEASE_VERSION}/uv-{target}.tar.gz']


def test_github_checksum_mismatch_never_installs(monkeypatch, tmp_path):
    archive = uv_archive('aarch64-apple-darwin', macho('arm64'))
    setup_release(monkeypatch, tmp_path, archive)
    monkeypatch.setitem(installer.UV_RELEASE_SHA256, 'aarch64-apple-darwin', '0' * 64)
    with pytest.raises(installer.InstallationError, match='SHA256'):
        installer._install_uv_standalone()
    assert not installer.uv_install_path('aarch64-apple-darwin').exists()


@pytest.mark.parametrize('name,kind', [
    ('../../outside', None), ('/outside', None),
    ('uv-aarch64-apple-darwin/uv', b'2'),
    ('uv-aarch64-apple-darwin/uv', b'1'),
])
def test_github_archive_rejects_paths_and_links(monkeypatch, tmp_path, name, kind):
    archive = uv_archive('aarch64-apple-darwin', macho('arm64'), member_name=name, kind=kind)
    setup_release(monkeypatch, tmp_path, archive)
    with pytest.raises(installer.InstallationError, match='archive'):
        installer._install_uv_standalone()
    assert not installer.uv_install_path('aarch64-apple-darwin').exists()


def test_bootstrap_preserves_existing_files(monkeypatch, tmp_path):
    archive = uv_archive('aarch64-apple-darwin', macho('arm64'))
    setup_release(monkeypatch, tmp_path, archive)
    installed = installer.uv_install_path('aarch64-apple-darwin')
    installed.parent.mkdir(parents=True)
    installed.write_text('existing file')
    local_uv = tmp_path / '.local/bin/uv'
    local_uv.parent.mkdir(parents=True)
    local_uv.write_bytes(macho('x86_64'))
    with pytest.raises(installer.InstallationError, match='overwrite'):
        installer._install_uv_standalone()
    assert installed.read_text() == 'existing file'
    assert local_uv.read_bytes() == macho('x86_64')


@pytest.mark.parametrize('endian,size,magic', [
    ('>', 20, b'\xca\xfe\xba\xbe'), ('<', 20, b'\xbe\xba\xfe\xca'),
    ('>', 32, b'\xca\xfe\xba\xbf'), ('<', 32, b'\xbf\xba\xfe\xca'),
])
def test_universal_uv_supports_both_mac_architectures(tmp_path, endian, size, magic):
    import struct
    header = magic + struct.pack(endian + 'I', 2)
    for cpu in (0x01000007, 0x0100000C):
        header += struct.pack(endian + 'I', cpu) + b'\0' * (size - 4)
    uv = tmp_path / 'uv'
    uv.write_bytes(header)
    assert installer.uv_matches_target(uv, 'aarch64-apple-darwin')
    assert installer.uv_matches_target(uv, 'x86_64-apple-darwin')


def test_shadowed_intel_uv_does_not_hide_native_uv_later_in_path(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(installer, 'uv_target', lambda: 'aarch64-apple-darwin')
    for folder, arch in [('intel', 'x86_64'), ('native', 'arm64')]:
        uv = tmp_path / folder / 'uv'
        uv.parent.mkdir()
        uv.write_bytes(macho(arch))
        uv.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path / 'intel') + ':' + str(tmp_path / 'native'))
    assert installer.find_uv() == str(tmp_path / 'native/uv')


def test_script_download_failure_falls_back_without_polluting_destination(monkeypatch, tmp_path):
    archive = uv_archive('aarch64-apple-darwin', macho('arm64'))
    setup_release(monkeypatch, tmp_path, archive)
    downloads = []
    def download(url, timeout):
        downloads.append(url)
        return io.BytesIO(b'#!/bin/sh\nexit 1\n' if url == installer.UV_INSTALL_URL else archive)
    monkeypatch.setattr(installer.urllib.request, 'urlopen', download)
    staged = []
    monkeypatch.setenv('UV_UNMANAGED_INSTALL', '/do-not-use')
    monkeypatch.setenv('UV_INSTALLER_GITHUB_BASE_URL', 'https://do-not-use.invalid')
    def failed_script(command, **kwargs):
        staged.append(Path(command[-1]).parent)
        assert kwargs['env']['UV_UNMANAGED_INSTALL'] == kwargs['env']['UV_INSTALL_DIR']
        assert 'UV_INSTALLER_GITHUB_BASE_URL' not in kwargs['env']
        raise installer.subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(installer.subprocess, 'run', failed_script)
    installer._install_uv_standalone()
    assert len(downloads) == 2
    assert installer.uv_install_path('aarch64-apple-darwin').read_bytes() == macho('arm64')
    assert all(not directory.exists() for directory in staged)


def test_github_download_limit_prevents_install(monkeypatch, tmp_path):
    archive = uv_archive('aarch64-apple-darwin', macho('arm64'))
    setup_release(monkeypatch, tmp_path, archive)
    monkeypatch.setattr(installer, 'UV_ARCHIVE_LIMIT', 10)
    with pytest.raises(installer.InstallationError, match='size limit'):
        installer._install_uv_standalone()
    assert not installer.uv_install_path('aarch64-apple-darwin').exists()


def test_github_archive_rejects_wrong_native_architecture(monkeypatch, tmp_path):
    archive = uv_archive('aarch64-apple-darwin', macho('x86_64'))
    setup_release(monkeypatch, tmp_path, archive)
    with pytest.raises(installer.InstallationError, match='native macOS architecture'):
        installer._install_uv_standalone()
    assert not installer.uv_install_path('aarch64-apple-darwin').exists()
