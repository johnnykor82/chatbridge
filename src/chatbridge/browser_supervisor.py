"""Manage one independently owned Chrome: private profile, dynamic CDP, recovery.

Never attach to Chrome from Mac Control or another owner. An orphan is adopted
only if its identity matches an ownership record created by this supervisor.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)
MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class ProfileInUse(RuntimeError):
    """A different browser owner is using this profile."""


class ChromeSupervisor:
    def __init__(
        self,
        profile: Path,
        chrome: Path | None = None,
        *,
        startup_timeout: float = 35,
        check_interval: float = 4,
        on_ready=None,
        headless: bool = False,
    ):
        self.profile = Path(profile).expanduser().resolve()
        self.chrome = Path(chrome or MAC_CHROME).expanduser()
        self.startup_timeout = startup_timeout
        self.check_interval = check_interval
        self.on_ready = on_ready
        self.headless = headless
        self._debug_port: int | None = None
        self.process: subprocess.Popen | None = None
        self._adopted_pid: int | None = None
        self.cdp_url: str | None = None
        self.last_error: str | None = None
        self._lockfile = None
        self._watchdog: asyncio.Task | None = None
        self._stopping = False
        self._restarts = 0

    def _identity(self, pid: int) -> dict | None:
        if pid <= 1:
            return None
        try:
            cmd = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="], text=True
            ).strip()
            start = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart="], text=True
            ).strip()
            if (
                not cmd.startswith(str(self.chrome))
                or f"--user-data-dir={self.profile}" not in cmd
                or "--remote-debugging-port=0" in cmd
                or not any(
                    token.startswith("--remote-debugging-port=")
                    and token.split("=", 1)[1].isdigit()
                    and int(token.split("=", 1)[1]) > 0
                    and (self._debug_port is None or int(token.split("=", 1)[1]) == self._debug_port)
                    for token in cmd.split()
                )
                or ("--headless=new" in cmd) != self.headless
                or os.getpgid(pid) != pid
            ):
                return None
            return {
                "pid": pid,
                "start": start,
                "profile": str(self.profile),
                "chrome": str(self.chrome),
            }
        except (OSError, subprocess.CalledProcessError):
            return None

    def _active_pid(self) -> int | None:
        if self.process and self.process.poll() is None:
            return self.process.pid
        if self._adopted_pid and self._identity(self._adopted_pid):
            return self._adopted_pid
        return None

    def status(self) -> dict:
        pid = self._active_pid()
        return {
            "mode": "managed",
            "headless": self.headless,
            "effective_mode": "headless" if self.headless else "visible_background",
            "focus_free": True,
            "state": (
                "stopped"
                if self._stopping
                else "running"
                if pid and self.cdp_url
                else "starting"
                if pid
                else "unavailable"
            ),
            "owned": self._lockfile is not None,
            "pid": pid,
            "adopted": bool(pid and pid == self._adopted_pid),
            "cdp_connected": bool(self.cdp_url and pid),
            "cdp_url": self.cdp_url,
            "profile": str(self.profile),
            "restart_count": self._restarts,
            "last_error": self.last_error,
        }

    def _owner_file(self) -> Path:
        return self.profile / ".chatbridge-browser-owner.json"

    def _acquire_profile(self) -> None:
        if not self.chrome.is_file():
            raise FileNotFoundError(f"Chrome not installed: {self.chrome}")
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile.chmod(0o700)
        path = self.profile / ".chatbridge-owner.lock"
        fp = path.open("a+")
        try:
            os.chmod(path, 0o600)
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            fp.close()
            raise ProfileInUse(f"Browser profile locked by another owner: {self.profile}") from exc
        self._lockfile = fp

    @staticmethod
    def _endpoint(profile: Path) -> tuple[str, str] | None:
        try:
            port, path = (profile / "DevToolsActivePort").read_text().splitlines()[:2]
            port = int(port)
        except (OSError, ValueError):
            return None
        if not 1 <= port <= 65535 or not path.startswith("/devtools/browser/"):
            return None
        return f"http://127.0.0.1:{port}", path

    @staticmethod
    def _fixed_port_endpoint(port: int) -> tuple[str, str] | None:
        """Discover websocket identity on a nonzero CDP port, without port=0."""
        if not isinstance(port, int) or not 1 <= port <= 65535:
            return None
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(base + "/json/version", timeout=2) as response:
                ws = json.load(response).get("webSocketDebuggerUrl", "")
            prefix = f"ws://127.0.0.1:{port}"
            if ws.startswith(prefix + "/devtools/browser/"):
                return base, ws[len(prefix):]
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return None

    def _current_endpoint(self) -> tuple[str, str] | None:
        """Resolve the managed browser's verified nonzero port (or legacy file)."""
        if self._debug_port:
            return self._fixed_port_endpoint(self._debug_port)
        try:
            owner = json.loads(self._owner_file().read_text())
            port = owner.get("debug_port")
            if isinstance(port, int) and port > 0:
                return self._fixed_port_endpoint(port)
        except (OSError, ValueError, TypeError):
            pass
        return self._endpoint(self.profile)

    @staticmethod
    def _choose_debug_port() -> int:
        """Let the OS allocate a free port; Chrome is never passed port=0."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port <= 0:
            raise RuntimeError("Unable to reserve a nonzero Chrome CDP port")
        return port

    @staticmethod
    def _probe(endpoint: str, browser_path: str) -> bool:
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=2) as response:
                data = json.load(response)
            ws = data.get("webSocketDebuggerUrl", "")
            return ws.startswith("ws://127.0.0.1:") and ws.endswith(browser_path)
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def _write_owner(self, pid: int, cdp_path: str) -> None:
        identity = self._identity(pid)
        if identity is None:
            raise RuntimeError("Launched Chrome process identity verification failed")
        identity["cdp_path"] = cdp_path
        identity["debug_port"] = self._debug_port
        temp = self.profile / ".chatbridge-owner-writing.json"
        with temp.open("w") as stream:
            os.chmod(temp, 0o600)
            json.dump(identity, stream)
        temp.replace(self._owner_file())

    def _verified_orphan(self, cdp_path: str) -> int | None:
        try:
            record = json.loads(self._owner_file().read_text())
            pid = record.get("pid")
            if not isinstance(pid, int) or record.get("cdp_path") != cdp_path:
                return None
            identity = self._identity(pid)
            if identity and all(record.get(key) == val for key, val in identity.items()):
                debug_port = record.get("debug_port")
                if isinstance(debug_port, int) and debug_port > 0:
                    self._debug_port = debug_port
                return pid
        except (OSError, ValueError, TypeError):
            pass
        return None

    async def _notify_ready(self):
        if self.on_ready and self.cdp_url:
            result = self.on_ready(self.cdp_url)
            if asyncio.iscoroutine(result):
                await result

    async def _terminate_owned(self) -> None:
        pid = self._active_pid()
        proc = self.process
        if pid and self._identity(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(35):
                if self._identity(pid) is None:
                    break
                await asyncio.sleep(0.2)
            else:
                if self._identity(pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        if proc:
            try:
                await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
            except (ChildProcessError, asyncio.TimeoutError):
                pass
        self.process = None
        self._adopted_pid = None
        self.cdp_url = None
        self._debug_port = None

    async def _launch(self):
        if self._active_pid() and self.cdp_url:
            return
        if self._active_pid() and not self.cdp_url:
            await self._terminate_owned()
        previous = self._current_endpoint()
        if previous and await asyncio.to_thread(self._probe, *previous):
            owned_pid = self._verified_orphan(previous[1])
            if owned_pid is None:
                raise ProfileInUse("An active Chrome has no verified ChatBridge ownership record")
            self.process = None
            self._adopted_pid = owned_pid
            self.cdp_url = previous[0]
            self.last_error = None
            log.info("Adopted own orphan Chrome: pid=%d cdp=%s", owned_pid, self.cdp_url)
            await self._notify_ready()
            return
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        self._debug_port = self._choose_debug_port()
        args = [
            str(self.chrome),
            f"--user-data-dir={self.profile}",
            f"--remote-debugging-port={self._debug_port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            # Headed Chrome starts with NO window; page creation via
            # Target.createTarget(background=True, focus=False) produces a
            # visible tab without activating the macOS application.
            *(["--headless=new", "--disable-gpu"] if self.headless else ["--no-startup-window"]),
            # Owned request tabs navigate to ChatGPT only on demand.
            *(["about:blank"] if self.headless else []),
        ]
        self._adopted_pid = None
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        self.cdp_url = None
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Chrome exited: {self.process.returncode}")
            port = self._current_endpoint()
            if port and await asyncio.to_thread(self._probe, *port):
                self._write_owner(self.process.pid, port[1])
                self.cdp_url = port[0]
                self.last_error = None
                log.info("Managed Chrome ready: pid=%d cdp=%s", self.process.pid, self.cdp_url)
                await self._notify_ready()
                return
            await asyncio.sleep(0.25)
        raise TimeoutError("Owned Chrome did not publish a verified CDP endpoint")

    async def start(self) -> None:
        if self._lockfile is not None:
            return
        self._stopping = False
        self._acquire_profile()
        try:
            await self._launch()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Managed Chrome startup failed: %s", self.last_error)
        self._watchdog = asyncio.create_task(self._monitor(), name="chatbridge-browser-watchdog")

    async def _monitor(self):
        delay = self.check_interval
        try:
            while not self._stopping:
                await asyncio.sleep(delay)
                pid = self._active_pid()
                if pid and self.cdp_url:
                    current = self._current_endpoint()
                    if (
                        current
                        and current[0] == self.cdp_url
                        and await asyncio.to_thread(self._probe, *current)
                    ):
                        continue
                self.cdp_url = None
                self._restarts += 1
                try:
                    await self._launch()
                    delay = self.check_interval
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    log.warning("Managed Chrome recovery failed: %s", self.last_error)
                    delay = min(max(delay * 2, self.check_interval), 60)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self._stopping = True
        if self._watchdog:
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)
            self._watchdog = None
        await self._terminate_owned()
        if self._lockfile is not None:
            self._owner_file().unlink(missing_ok=True)
            fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_UN)
            self._lockfile.close()
            self._lockfile = None
