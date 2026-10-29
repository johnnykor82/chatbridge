#!/usr/bin/env python3
"""macOS per-user installer for independent ChatBridge releases.

No Mac Control, Watcher, browser-cookie export or privileged shell commands.
Prompts before provisioning missing uv; requires Google Chrome. Never mutates
an active service until a release was installed and checked. Python 3.9+ stdlib.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.parsers.expat import ExpatError

SOURCE = Path(__file__).resolve().parents[1]
# Explicit runtime allowlist. Development-only operational notes and private
# local infrastructure runbooks are intentionally *not* included in releases.
MANIFEST = (
    "src/chatbridge/__init__.py",
    "src/chatbridge/__main__.py",
    "src/chatbridge/artifact_export.py",
    "src/chatbridge/background_browser.py",
    "src/chatbridge/background_http.py",
    "src/chatbridge/browser_supervisor.py",
    "src/chatbridge/browser_transport.py",
    "src/chatbridge/cleanup_ops.py",
    "src/chatbridge/cli.py",
    "src/chatbridge/config.py",
    "src/chatbridge/engine.py",
    "src/chatbridge/marker_lookup.py",
    "src/chatbridge/media.py",
    "src/chatbridge/mock_transport.py",
    "src/chatbridge/model.py",
    "src/chatbridge/poll_policy.py",
    "src/chatbridge/project_evidence.py",
    "src/chatbridge/rate_limit.py",
    "src/chatbridge/recovery.py",
    "src/chatbridge/server.py",
    "src/chatbridge/store.py",
    "docs/INSTALL.md",
    "docs/API_REFERENCE.md",
    "docs/ATTACHMENTS.md",
    "docs/IMAGES.md",
    "pyproject.toml",
    "uv.lock",
    "LICENSE",
    "SECURITY.md",
    "README.md",
    "README.ru.md",
)
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DEFAULT_LABEL = "net.chatbridge.gateway"
INSTALLER_VERSION = "1"
TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.-]{2,110}$")


class InstallationError(Exception):
    pass


def app_home():
    return Path.home() / ".local" / "share" / "chatbridge"


def state_home():
    return Path.home() / ".chatbridge"


def agent_directory():
    return Path.home() / "Library" / "LaunchAgents"


def run(args, *, check=True, capture=False):
    return subprocess.run(
        list(map(str, args)),
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def paths(args):
    prefix = Path(args.prefix).expanduser().resolve() if args.prefix else app_home()
    state = Path(args.state_dir).expanduser().resolve() if args.state_dir else state_home()
    agents = agent_directory()
    if not TOKEN_RE.fullmatch(args.label):
        raise InstallationError("Invalid LaunchAgent label")
    return prefix, state, agents / (args.label + ".plist")


def require_os():
    if sys.platform != "darwin":
        raise InstallationError(
            "The LaunchAgent installer requires macOS; use uv run chatbridge on Linux"
        )
    if os.geteuid() == 0:
        raise InstallationError("Do not use sudo. Install a per-user LaunchAgent.")


UV_INSTALL_URL = "https://astral.sh/uv/install.sh"
# Pinned official release digests from the GitHub release API. Never trust a
# checksum downloaded alongside the archive (ASVS V15.2 dependency integrity).
UV_RELEASE_VERSION = "0.13.6"
UV_RELEASE_SHA256 = {
    "aarch64-apple-darwin": "60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472",
    "x86_64-apple-darwin": "63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02",
}
UV_ARCHIVE_LIMIT = 64 * 1024 * 1024
UV_BINARY_LIMIT = 128 * 1024 * 1024


def uv_target():
    """Find the hardware architecture, including Python running under Rosetta."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "aarch64-apple-darwin"
    if machine == "x86_64":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
                capture_output=True, text=True, check=False, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                return "aarch64-apple-darwin"
        except (OSError, subprocess.TimeoutExpired):
            pass
        return "x86_64-apple-darwin"
    raise InstallationError("Unsupported macOS architecture: " + machine)


def uv_install_path(target):
    return app_home() / "tools" / "uv" / target / "uv"


def uv_matches_target(path, target):
    """Read Mach-O headers without executing a foreign-architecture program."""
    cpu = 0x0100000C if target == "aarch64-apple-darwin" else 0x01000007
    try:
        with Path(path).open("rb") as source:
            header = source.read(4096)
    except OSError:
        return False
    if len(header) < 8:
        return False
    magic = header[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
        endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
        return struct.unpack(endian + "I", header[4:8])[0] == cpu
    fat = {
        b"\xca\xfe\xba\xbe": (">", 20), b"\xbe\xba\xfe\xca": ("<", 20),
        b"\xca\xfe\xba\xbf": (">", 32), b"\xbf\xba\xfe\xca": ("<", 32),
    }
    if magic not in fat:
        return False
    endian, size = fat[magic]
    count = struct.unpack(endian + "I", header[4:8])[0]
    if not 1 <= count <= 32 or len(header) < 8 + count * size:
        return False
    return any(
        struct.unpack_from(endian + "I", header, 8 + index * size)[0] == cpu
        for index in range(count)
    )


def find_uv():
    """Skip Intel-only uv on Apple Silicon, including when it shadows PATH."""
    target = uv_target()
    candidates = [Path(folder) / "uv" for folder in os.get_exec_path()]
    candidates.extend((
        uv_install_path(target), Path.home() / ".local/bin/uv",
        Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv"),
    ))
    for candidate in candidates:
        if (candidate.is_file() and os.access(candidate, os.X_OK)
                and uv_matches_target(candidate, target)):
            return str(candidate)
    return None


def _publish_uv(source, target):
    if not uv_matches_target(source, target):
        raise InstallationError("Downloaded uv does not support the native macOS architecture")
    destination = uv_install_path(target)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Link an already complete file atomically. An existing file or symlink is
    # never overwritten, and an interrupted copy cannot become discoverable.
    with tempfile.TemporaryDirectory(prefix=".uv-", dir=destination.parent) as folder:
        staged = Path(folder) / "uv"
        shutil.copyfile(source, staged)
        staged.chmod(0o755)
        try:
            os.link(staged, destination)
        except FileExistsError as exc:
            raise InstallationError("Refusing to overwrite existing uv: " + str(destination)) from exc


def _install_uv_release(target):
    """Verify a pinned GitHub tarball and read only the expected regular file."""
    root = "uv-" + target
    url = ("https://github.com/astral-sh/uv/releases/download/"
           + UV_RELEASE_VERSION + "/" + root + ".tar.gz")
    print("Downloading verified uv release: " + url, flush=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read(UV_ARCHIVE_LIMIT + 1)
    if len(archive) > UV_ARCHIVE_LIMIT:
        raise InstallationError("uv archive exceeds the download size limit")
    if hashlib.sha256(archive).hexdigest() != UV_RELEASE_SHA256[target]:
        raise InstallationError("uv archive SHA256 verification failed")
    with tempfile.TemporaryDirectory(prefix="chatbridge-uv-") as folder:
        source = Path(folder) / "uv"
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            seen = set()
            for member in bundle:
                # No extractall: forbid traversal, links, duplicates, special
                # files and unexpected entries before writing anything.
                if member.name == root and member.isdir() and member.name not in seen:
                    seen.add(member.name)
                    continue
                if (member.name not in (root + "/uv", root + "/uvx")
                        or member.name in seen or not member.isfile()
                        or not 0 < member.size <= UV_BINARY_LIMIT):
                    raise InstallationError("Unsafe or unexpected uv archive member")
                seen.add(member.name)
                if member.name == root + "/uv":
                    with bundle.extractfile(member) as binary:
                        source.write_bytes(binary.read(UV_BINARY_LIMIT + 1))
            if not source.is_file():
                raise InstallationError("uv archive is missing the uv executable")
        _publish_uv(source, target)


def _install_uv_standalone():
    """Stage the official installer, falling back to a pinned verified binary."""
    target = uv_target()
    print("Downloading official uv installer: " + UV_INSTALL_URL, flush=True)
    try:
        try:
            with urllib.request.urlopen(UV_INSTALL_URL, timeout=30) as response:
                script = response.read(512 * 1024 + 1)
        except (OSError, urllib.error.URLError, TimeoutError):
            print("Official installer download failed; trying the verified GitHub release.")
            _install_uv_release(target)
            return
        if not script or len(script) > 512 * 1024 or b"\x00" in script:
            raise InstallationError("Official uv installer download was empty or unexpectedly large")
        with tempfile.TemporaryDirectory(prefix="chatbridge-uv-") as folder:
            path = Path(folder) / "install.sh"
            path.write_bytes(script)
            bin_dir = Path(folder) / "bin"
            # Installer-specific overrides must not redirect a staged install
            # or its downloads through user-defined mirrors.
            run_env = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
            run_env["UV_NO_MODIFY_PATH"] = "1"
            run_env["UV_INSTALL_DIR"] = str(bin_dir)
            # Unmanaged mode also suppresses the installation receipt and
            # self-updater, both of which would otherwise escape staging.
            run_env["UV_UNMANAGED_INSTALL"] = str(bin_dir)
            command = ["/bin/sh", str(path)]
            if target == "aarch64-apple-darwin":
                command = ["/usr/bin/arch", "-arm64"] + command
            try:
                subprocess.run(command, check=True, env=run_env)
            except subprocess.CalledProcessError:
                print("Official installer failed; trying the verified GitHub release.")
                _install_uv_release(target)
                return
            _publish_uv(bin_dir / "uv", target)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError,
            tarfile.TarError, subprocess.CalledProcessError) as exc:
        raise InstallationError(
            "Could not install uv using the official installer: " + str(exc)
            + "\nManual installation: https://docs.astral.sh/uv/getting-started/installation/"
        ) from exc


def require_uv():
    found = find_uv()
    if found:
        return found

    print("\nChatBridge needs 'uv' to create an isolated Python environment.")
    print("The official standalone installer can be used, without Homebrew:")
    print("  " + UV_INSTALL_URL)
    print("The installer script will be downloaded and executed as your user.")
    print("uv will be installed at " + str(uv_install_path(uv_target())))
    print("Your shell configuration will not be modified.")

    if not sys.stdin.isatty():
        raise InstallationError(
            "uv is not installed and input is not interactive. "
            "Run ./install.sh in Terminal to be offered installation, "
            "or install uv yourself: https://docs.astral.sh/uv/getting-started/installation/"
        )

    try:
        answer = input("Install uv now using the official standalone installer? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise InstallationError(
            "uv installation was cancelled. Manual installation: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        ) from None
    if answer not in ("y", "yes"):
        raise InstallationError(
            "uv installation declined. To install it manually, see "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )

    print("Installing uv...", flush=True)
    _install_uv_standalone()

    found = find_uv()
    if not found:
        raise InstallationError(
            "uv installer finished but the executable was not found. "
            "Try opening a new Terminal or consult "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )
    print("uv ready: " + found + ". Continuing ChatBridge installation.", flush=True)
    return found


def inspect_plist(path):
    try:
        with path.open("rb") as source:
            return plistlib.load(source)
    except (OSError, ValueError, ExpatError, plistlib.InvalidFileException):
        return None


def configured_services(except_plist):
    """Enumerate local LaunchAgents to prevent two owners of one Chrome profile."""
    for candidate in agent_directory().glob("*.plist"):
        if candidate == except_plist:
            continue
        data = inspect_plist(candidate)
        if isinstance(data, dict) and isinstance(data.get("EnvironmentVariables"), dict):
            yield candidate, data


def refuse_shared_state(own_plist, state):
    database = (state / "state.db").resolve()
    profile = (state / "chrome-profile").resolve()
    for candidate, data in configured_services(own_plist):
        env = data["EnvironmentVariables"]
        db = env.get("CHATBRIDGE_DB")
        chrome = env.get("CHATBRIDGE_CHROME_PROFILE")
        if (db and Path(db).expanduser().resolve() == database) or (
            chrome and Path(chrome).expanduser().resolve() == profile
        ):
            raise InstallationError(
                "Data or Chrome profile is already assigned to LaunchAgent "
                + str(data.get("Label", candidate.stem))
                + ". Use --state-dir to isolate it."
            )


def refuse_remove_shared_app(own_plist, prefix):
    releases = (prefix / "releases").resolve()
    for candidate, data in configured_services(own_plist):
        directory = data.get("WorkingDirectory", "")
        if directory:
            release = Path(directory).expanduser().resolve()
            if releases in release.parents or release == releases:
                raise InstallationError(
                    "Application releases are used by another LaunchAgent: "
                    + str(data.get("Label", candidate.stem))
                )


def assert_ours(data, label):
    if (
        not data
        or data.get("Label") != label
        or data.get("ChatBridgeInstaller") != INSTALLER_VERSION
    ):
        raise InstallationError(
            "Existing service was not created by this installer. "
            "Refusing to overwrite it; use a separate --label."
        )


def version_tag():
    content = hashlib.sha256()
    for name in MANIFEST:
        item = SOURCE / name
        if not item.is_file():
            raise InstallationError("Incomplete source checkout: " + str(item))
        content.update(name.encode())
        content.update(item.read_bytes())
    version = re.search(r'^version\s*=\s*"([^"]+)"', (SOURCE / "pyproject.toml").read_text(), re.M)
    if not version:
        raise InstallationError("Cannot read application version")
    return version.group(1) + "-" + content.hexdigest()[:12]


def release_files(target):
    for name in MANIFEST:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, destination)


def build_release(uv, prefix):
    releases = prefix / "releases"
    release = releases / version_tag()
    marker = release / ".chatbridge-ready"
    if marker.is_file() and (release / ".venv/bin/python").is_file():
        return release, False
    if release.exists():
        # An interrupted/incomplete build must never be treated as a valid release.
        shutil.rmtree(release)
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    release.mkdir(mode=0o700)
    try:
        release_files(release)
        run(
            [
                uv,
                "sync",
                "--project",
                release,
                "--locked",
                "--no-dev",
                "--no-editable",
                "--python",
                "3.13",
            ]
        )
        python = release / ".venv/bin/python"
        if not python.is_file():
            raise InstallationError("uv did not install the Python environment")
        run(
            [
                python,
                "-c",
                "import chatbridge, aiohttp, playwright; "
                "from chatbridge.config import Settings; "
                "from chatbridge.browser_supervisor import ChromeSupervisor",
            ]
        )
        marker.write_text("installer=" + INSTALLER_VERSION + "\n")
        return release, True
    except Exception:
        shutil.rmtree(release, ignore_errors=True)
        raise


def plist_data(args, release, state, chrome):
    setting = {
        "CHATBRIDGE_BROWSER_MODE": "managed",
        "CHATBRIDGE_CHROME_PROFILE": str(state / "chrome-profile"),
        "CHATBRIDGE_DB": str(state / "state.db"),
        "CHATBRIDGE_HOST": args.host,
        "CHATBRIDGE_PORT": str(args.port),
        "CHATBRIDGE_MAX_ACTIVE": str(args.max_active),
        "CHATBRIDGE_REASONING_EFFORT": args.reasoning_effort,
        "CHATBRIDGE_CHROME_PATH": str(chrome),
    }
    if args.api_key:
        setting["CHATBRIDGE_API_KEY"] = args.api_key
    return {
        "Label": args.label,
        "ChatBridgeInstaller": INSTALLER_VERSION,
        "ProgramArguments": [str(release / ".venv/bin/python"), "-u", "-m", "chatbridge"],
        "WorkingDirectory": str(release),
        "EnvironmentVariables": setting,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "StandardOutPath": str(state / "service.stdout.log"),
        "StandardErrorPath": str(state / "service.stderr.log"),
    }


def write_plist(path, data):
    tmp = path.with_suffix(".plist.tmp")
    with tmp.open("wb") as stream:
        plistlib.dump(data, stream, sort_keys=True)
    tmp.chmod(0o600)
    os.replace(tmp, path)


def label_domain(label):
    return "gui/" + str(os.getuid()) + "/" + label


def is_loaded(label):
    status = run(["launchctl", "print", label_domain(label)], check=False, capture=True)
    return status.returncode == 0


def stop(label):
    if is_loaded(label):
        run(["launchctl", "bootout", label_domain(label)])


def start(label, plist):
    run(["launchctl", "enable", label_domain(label)])
    run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), plist])


def probe(host, port, url_path="/health", timeout=2, api_key=""):
    try:
        headers = {"Authorization": "Bearer " + api_key} if api_key else {}
        req = urllib.request.Request("http://" + host + ":" + str(port) + url_path, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def fetch_json(host, port, path, *, timeout=4, api_key=""):
    headers = {"Authorization": "Bearer " + api_key} if api_key else {}
    request = urllib.request.Request("http://" + host + ":" + str(port) + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def busy(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def login_command(chrome, profile):
    """Open a window in the existing managed profile, without exposing credentials."""
    return shlex.join([
        str(chrome), "--user-data-dir=" + str(profile),
        "--new-window", "https://chatgpt.com/",
    ])


def do_install(args, *, update=False):
    require_os()
    # Read the existing plist *before* resolving upgrade defaults. Preserve
    # custom host, port, data directory, Chrome, concurrency and credentials.
    _, _, plist = paths(args)
    existing = inspect_plist(plist)
    if plist.exists():
        if not update:
            raise InstallationError(
                "LaunchAgent exists. Use 'upgrade' for an installer-managed service."
            )
        assert_ours(existing, args.label)
    elif update:
        raise InstallationError("No installer-managed service exists. Run install first.")
    original = (existing or {}).get("EnvironmentVariables", {})
    if update and existing:
        if args.prefix is None:
            args.prefix = str(Path(existing["WorkingDirectory"]).parent.parent)
        if args.state_dir is None:
            args.state_dir = str(Path(original["CHATBRIDGE_DB"]).parent)
    args.host = args.host or original.get("CHATBRIDGE_HOST", "127.0.0.1")
    args.port = args.port if args.port is not None else int(original.get("CHATBRIDGE_PORT", 8787))
    args.max_active = (
        args.max_active
        if args.max_active is not None
        else int(original.get("CHATBRIDGE_MAX_ACTIVE", 10))
    )
    args.reasoning_effort = args.reasoning_effort or original.get(
        "CHATBRIDGE_REASONING_EFFORT", "high"
    )
    args.api_key = (
        args.api_key
        if args.api_key is not None
        else os.getenv("CHATBRIDGE_API_KEY", original.get("CHATBRIDGE_API_KEY", ""))
    )
    args.chrome_path = args.chrome_path or original.get("CHATBRIDGE_CHROME_PATH")
    prefix, state, plist = paths(args)
    refuse_shared_state(plist, state)
    if is_loaded(args.label) and args.no_start:
        raise InstallationError("--no-start cannot replace a loaded LaunchAgent")
    chrome = Path(args.chrome_path).expanduser().resolve() if args.chrome_path else CHROME
    if not chrome.is_file():
        raise InstallationError("Google Chrome not found: " + str(chrome))
    if args.host != "127.0.0.1" and not args.api_key:
        raise InstallationError("Binding outside loopback requires --api-key")
    if not (1 <= args.port <= 65535 and 1 <= args.max_active <= 100):
        raise InstallationError("Invalid port or concurrency setting")
    if not update and busy(args.host, args.port):
        raise InstallationError("HTTP port " + str(args.port) + " is already in use")
    # Dry-run must never download or install dependencies or prompt the user.
    uv = find_uv() if args.dry_run else require_uv()
    if args.dry_run:
        if not uv:
            print("PLAN: uv is missing; a real interactive installation will offer to install it")
        print("CHECK OK: source=" + str(SOURCE))
        print("PLAN: build isolated immutable release under " + str(prefix / "releases"))
        print("PLAN: write plist at " + str(plist))
        print("PLAN: persistent Chrome and SQLite under " + str(state))
        print("PLAN: " + ("start via launchctl" if not args.no_start else "no service start"))
        return
    release, built = build_release(uv, prefix)
    print(("Built" if built else "Reusing") + " release: " + str(release))
    (prefix / ".chatbridge-managed").write_text(INSTALLER_VERSION + "\n")
    (prefix / ".chatbridge-managed").chmod(0o600)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    (state / ".chatbridge-installed").write_text(INSTALLER_VERSION + "\n")
    (state / ".chatbridge-installed").chmod(0o600)
    plist.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    previous_loaded = is_loaded(args.label)
    if plist.exists():
        backup_dir = state / "installer-backups"
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        backup = backup_dir / (args.label + "-" + str(time.time_ns()) + ".plist")
        shutil.copy2(plist, backup)
        backup.chmod(0o600)
    new_plist = plist_data(args, release, state, chrome)
    if args.no_start:
        write_plist(plist, new_plist)
    else:
        if previous_loaded:
            stop(args.label)
        try:
            write_plist(plist, new_plist)
            start(args.label, plist)
            for _ in range(120):
                if probe(
                    "127.0.0.1" if args.host == "0.0.0.0" else args.host,
                    args.port,
                    api_key=args.api_key,
                ):
                    break
                time.sleep(0.5)
            else:
                raise InstallationError("Service did not reach /health after start")
        except Exception:
            stop(args.label)
            if backup:
                shutil.copy2(backup, plist)
                plist.chmod(0o600)
                if previous_loaded:
                    start(args.label, plist)
            else:
                plist.unlink(missing_ok=True)
            raise
    print("Installed LaunchAgent: " + str(plist))
    print("HTTP: http://" + args.host + ":" + str(args.port))
    print("Chrome profile: " + str(state / "chrome-profile"))
    print("Logs: " + str(state / "service.stderr.log"))
    if args.no_start:
        print("Start: launchctl bootstrap gui/$(id -u) " + str(plist))
    elif not probe(
        "127.0.0.1" if args.host == "0.0.0.0" else args.host,
        args.port,
        "/ready",
        timeout=4,
        api_key=args.api_key,
    ):
        print("Chrome runs in the background; no login window opens automatically.")
        print("First check /v1/system/status: browser.mode must be managed and browser.state running.")
        print("Wait until then; do not open this profile while its managed Chrome is starting.")
        print("NEXT STEP: only after that check, run this command in Terminal and sign in:")
        print(login_command(chrome, state / "chrome-profile"))
        print("It opens a window in the same managed profile; reuse it if login is needed again.")
        print("Then check: curl -i http://127.0.0.1:" + str(args.port) + "/ready")
    else:
        print("ChatGPT authenticated: /ready HTTP 200")
    print("Chrome and user data remain yours. This is an unofficial web integration.")


def do_uninstall(args):
    require_os()
    prefix, state, plist = paths(args)
    if not plist.is_file():
        print("No ChatBridge LaunchAgent installed at " + str(plist))
        return
    data = inspect_plist(plist)
    assert_ours(data, args.label)
    if args.prefix is None:
        prefix = Path(data["WorkingDirectory"]).parent.parent
    if args.state_dir is None:
        state = Path(data["EnvironmentVariables"]["CHATBRIDGE_DB"]).parent
    if args.dry_run:
        print("PLAN: unload and remove " + str(plist))
        print("PLAN: preserve application releases and account data")
        if args.purge_data:
            print("PLAN: DELETE profile, SQLite and logs under " + str(state))
        return
    if args.purge_data and not args.yes:
        raise InstallationError(
            "--purge-data requires --yes (permanently deletes browser login and history)"
        )
    if args.purge_data and (
        state.is_symlink()
        or state in (Path.home(), Path("/"))
        or not (state / ".chatbridge-installed").is_file()
    ):
        raise InstallationError("Refusing to purge unowned or protected state directory")
    if args.remove_app:
        refuse_remove_shared_app(plist, prefix)
    if args.remove_app and (
        prefix.is_symlink()
        or prefix in (Path.home(), Path("/"))
        or not (prefix / ".chatbridge-managed").is_file()
    ):
        raise InstallationError("Refusing to remove an unrecognized app directory")
    stop(args.label)
    plist.unlink()
    print("LaunchAgent stopped and removed.")
    if not args.purge_data:
        print("Browser login and local database preserved.")
    if args.purge_data:
        shutil.rmtree(state)
        print("Deleted user data: " + str(state))
    if args.remove_app:
        shutil.rmtree(prefix)
        print("Removed application releases: " + str(prefix))


def do_status(args, *, doctor=False):
    prefix, state, plist = paths(args)
    data = inspect_plist(plist)
    if not data:
        result = {"installed": False, "plist": str(plist)}
        if doctor:
            result["next_action"] = "Run ./install.sh install"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    env = data.get("EnvironmentVariables", {})
    state = Path(env.get("CHATBRIDGE_DB", str(state / "state.db"))).parent
    host = env.get("CHATBRIDGE_HOST", "127.0.0.1")
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    port = int(env.get("CHATBRIDGE_PORT", "8787"))
    api_key = env.get("CHATBRIDGE_API_KEY", "")
    alive = probe(probe_host, port, api_key=api_key)
    system = (
        fetch_json(probe_host, port, "/v1/system/status", api_key=api_key)
        if doctor and alive else None
    )
    browser = (system or {}).get("browser", {})
    readiness = fetch_json(probe_host, port, "/ready", api_key=api_key) if alive else None
    ready = bool(readiness and readiness.get("ready"))
    result = {
        "installed": True,
        "installer_managed": data.get("ChatBridgeInstaller") == INSTALLER_VERSION,
        "launchagent_loaded": is_loaded(args.label) if sys.platform == "darwin" else False,
        "http_alive": alive,
        "chatgpt_ready": ready,
        "chatgpt_authenticated": (readiness.get("chatgpt_authenticated") if readiness else None),
        "api_url": "http://" + host + ":" + str(port),
        "browser_mode": env.get("CHATBRIDGE_BROWSER_MODE", "unknown"),
        "chrome_profile": env.get("CHATBRIDGE_CHROME_PROFILE"),
        "release_directory": data.get("WorkingDirectory"),
        "state_directory": str(state),
    }
    if doctor:
        python = Path(data["ProgramArguments"][0])
        chrome = Path(env.get("CHATBRIDGE_CHROME_PATH", str(CHROME)))
        result["python_installed"] = python.is_file()
        result["chrome_installed"] = chrome.is_file()
        if (system or {}).get("transport") == "mock" or (
            readiness and readiness.get("chatgpt_authenticated") is None
        ):
            result["next_action"] = "Mock-only transport; no ChatGPT login checked"
        elif not result["python_installed"]:
            result["next_action"] = "Run ./install.sh upgrade to restore Python environment"
        elif not result["chrome_installed"]:
            result["next_action"] = "Install Google Chrome, or use --chrome-path"
        elif not result["launchagent_loaded"] or not alive:
            result["next_action"] = "Check launchctl and service.stderr.log"
        elif browser.get("mode") != "managed" or browser.get("state") != "running":
            result["next_action"] = (
                "Wait for /v1/system/status browser.state=running before opening "
                "the managed profile; inspect service.stderr.log if it stays unavailable."
            )
        elif not ready or readiness.get("chatgpt_authenticated") is not True:
            result["next_action"] = (
                "Chrome runs in the background. Confirm /v1/system/status reports "
                "browser.mode=managed and browser.state=running, then run login_command in Terminal, "
                "then sign in to ChatGPT in that managed profile."
            )
            result["login_command"] = login_command(
                chrome, env.get("CHATBRIDGE_CHROME_PROFILE", str(state / "chrome-profile"))
            )
        else:
            result["next_action"] = "Healthy and authenticated"
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ChatBridge standalone macOS installer (no Mac Control dependency)"
    )
    cmd = parser.add_subparsers(dest="command", required=True)
    for action in ("install", "upgrade", "status", "uninstall", "doctor"):
        sub = cmd.add_parser(action)
        sub.add_argument("--prefix", help="Application release storage location")
        sub.add_argument("--state-dir", help="Persistent data and browser profile directory")
        sub.add_argument("--label", default=DEFAULT_LABEL)
        sub.add_argument("--dry-run", action="store_true")
        if action in ("install", "upgrade"):
            sub.add_argument("--host")
            sub.add_argument("--port", type=int)
            sub.add_argument("--max-active", type=int)
            sub.add_argument("--reasoning-effort", choices=("low", "medium", "high"))
            sub.add_argument("--chrome-path")
            sub.add_argument(
                "--api-key",
                default=None,
                help="Bearer key for non-loopback exposure (prefer env var)",
            )
            sub.add_argument("--no-start", action="store_true")
        if action == "uninstall":
            sub.add_argument("--purge-data", action="store_true")
            sub.add_argument("--yes", action="store_true")
            sub.add_argument("--remove-app", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("install", "upgrade"):
            do_install(args, update=args.command == "upgrade")
        elif args.command == "uninstall":
            do_uninstall(args)
        else:
            do_status(args, doctor=args.command == "doctor")
    except (InstallationError, OSError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
