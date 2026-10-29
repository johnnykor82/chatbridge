"""Isolated agent browser sessions owned exclusively by ChatBridge.

Agent/browser sessions are NOT ChatGPT sessions. Playwright controls only private
contexts launched here; the logged-in ChatGPT profile is never exposed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

SID = re.compile(r"^[a-f0-9]{32}$")
TOKEN = re.compile(r"^([a-f0-9]{32})\.([a-f0-9]{64})$")
KEY = re.compile(r"^[a-zA-Z0-9_.:-]{8,180}$")
ACTIONS = {
    "navigate",
    "click",
    "fill",
    "press",
    "scroll",
    "select",
    "hover",
    "wait_for",
    "new_tab",
    "close_tab",
    "evaluate",
    "upload",
    "download",
}
FILE_LIMIT = 70 * 1024 * 1024


class BrowserError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(message)


@dataclass
class Session:
    sid: str
    persistent: bool
    requested_mode: str
    context: object
    tabs: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BackgroundBrowsers:
    def __init__(self, root: Path, channel: str = "chrome"):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise BrowserError("UNSAFE_PATH", "Browser state root is a symlink", 500)
        self.root.chmod(0o700)
        for name in ("profiles", "files"):
            (self.root / name).mkdir(mode=0o700, exist_ok=True)
        self.db = sqlite3.connect(self.root / "sessions.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
          sid TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
          persistent INTEGER NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
          created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (
          operation_id TEXT PRIMARY KEY, sid TEXT NOT NULL,
          request_key TEXT NOT NULL, action TEXT NOT NULL,
          payload_sha TEXT NOT NULL, state TEXT NOT NULL,
          result TEXT, error_code TEXT, created REAL NOT NULL, updated REAL NOT NULL,
          UNIQUE(sid,request_key));
        CREATE TABLE IF NOT EXISTS files (
          file_id TEXT PRIMARY KEY, sid TEXT NOT NULL, filename TEXT NOT NULL,
          mime TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL);
        """)
        with self.db:
            self.db.execute(
                "UPDATE operations SET state='unknown',error_code='RESTART_INTERRUPTED' WHERE state='running'"
            )
            self.db.execute("UPDATE sessions SET state='interrupted' WHERE state='open'")
        self.sessions: dict[str, Session] = {}
        self.supervisors = {}
        self.pw = None
        self.shared = None
        self.lifecycle = asyncio.Lock()
        self.channel = channel

    async def _playwright(self):
        if self.pw is None:
            self.pw = await async_playwright().start()
        return self.pw

    async def _context(self, sid: str, persistent: bool, mode: str):
        pw = await self._playwright()
        if mode in ("background", "visible_background"):
            from .browser_supervisor import ChromeSupervisor

            supervisor = ChromeSupervisor(self.root / "profiles" / sid, headless=False)
            await supervisor.start()
            if supervisor.status()["state"] != "running":
                error = supervisor.last_error or "Headed Chrome did not become ready"
                await supervisor.stop()
                raise BrowserError("BROWSER_UNAVAILABLE", error, 503)
            try:
                browser = await pw.chromium.connect_over_cdp(supervisor.cdp_url, timeout=15000)
                if not browser.contexts:
                    raise RuntimeError("No owned browser context")
            except BaseException:
                await supervisor.stop()
                raise
            self.supervisors[sid] = supervisor
            return browser.contexts[0]
        if mode == "foreground" and not persistent:
            raise BrowserError(
                "INVALID_MODE", "Explicit foreground mode requires isolated persistent profile"
            )
        if persistent:
            path = self.root / "profiles" / sid
            path.mkdir(mode=0o700, exist_ok=True)
            return await pw.chromium.launch_persistent_context(
                str(path),
                channel=self.channel,
                headless=mode != "foreground",
                accept_downloads=True,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        if self.shared is None or not self.shared.is_connected():
            self.shared = await pw.chromium.launch(
                channel=self.channel,
                headless=True,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        return await self.shared.new_context(accept_downloads=True)

    @staticmethod
    def _mode(mode: str) -> str:
        return "visible_background" if mode in ("background", "visible_background") else mode

    async def _new_page(self, agent):
        if self._mode(agent.requested_mode) != "visible_background":
            return await agent.context.new_page()
        browser = agent.context.browser
        session = await browser.new_browser_cdp_session()
        try:
            async with agent.context.expect_page(timeout=15000) as event:
                await session.send(
                    "Target.createTarget",
                    {
                        "url": "about:blank",
                        "background": True,
                        "focus": False,
                    },
                )
            return await event.value
        finally:
            await session.detach()

    async def _close_context(self, agent):
        supervisor = self.supervisors.pop(agent.sid, None)
        if supervisor:
            try:
                await agent.context.browser.close()  # CDP disconnect, not process kill
            finally:
                await supervisor.stop()  # kill only verified owned Chrome
        else:
            await agent.context.close()

    def _authorized(self, sid: str, token: str):
        row = (
            self.db.execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone()
            if SID.fullmatch(sid)
            else None
        )
        match = TOKEN.fullmatch(token) if isinstance(token, str) else None
        if not row or not match or match.group(1) != sid:
            raise BrowserError("NOT_AUTHORIZED", "Browser capability required", 403)
        if not hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise BrowserError("NOT_AUTHORIZED", "Invalid browser capability", 403)
        return row

    def _active(self, sid: str, token: str):
        row = self._authorized(sid, token)
        if row["state"] != "open" or sid not in self.sessions:
            raise BrowserError(
                "REQUIRES_REOPEN",
                "Persistent session must be reopened; temporary session has expired",
                409,
            )
        agent = self.sessions[sid]
        if agent.context.browser and not agent.context.browser.is_connected():
            with self.db:
                self.db.execute(
                    "UPDATE sessions SET state='interrupted',updated=? WHERE sid=?",
                    (time.time(), sid),
                )
            raise BrowserError("REQUIRES_REOPEN", "Owned Chrome is disconnected", 409)
        return agent

    async def create(self, *, persistent=False, mode="background", url="about:blank"):
        if type(persistent) is not bool or mode not in (
            "background",
            "headless",
            "visible_background",
            "foreground",
        ):
            raise BrowserError("INVALID_ARGUMENT", "Invalid browser settings")
        sid = secrets.token_hex(16)
        token = sid + "." + secrets.token_hex(32)
        if url != "about:blank" and not isinstance(url, str):
            raise BrowserError("INVALID_URL", "URL required")
        if url != "about:blank" and not url.startswith(("http://", "https://", "data:")):
            raise BrowserError("INVALID_URL", "HTTP(S) URL or data URL required")
        async with self.lifecycle:
            ctx = await self._context(sid, persistent, mode)
            agent = Session(sid, persistent, mode, ctx)
            try:
                page = ctx.pages[0] if ctx.pages else await self._new_page(agent)
                tid = self._register(agent, page)
                if url != "about:blank":
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except BaseException:
                await self._close_context(agent)
                if persistent:
                    shutil.rmtree(self.root / "profiles" / sid, ignore_errors=True)
                raise
            self.sessions[sid] = agent
            now = time.time()
            with self.db:
                self.db.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                    (
                        sid,
                        hashlib.sha256(token.encode()).hexdigest(),
                        int(persistent),
                        mode,
                        "open",
                        now,
                        now,
                    ),
                )
        return {
            "session_id": sid,
            "session_token": token,
            "tab_id": tid,
            "persist_profile": persistent,
            "requested_mode": mode,
            "effective_mode": self._mode(mode),
            "state": "open",
        }

    def _register(self, agent, page):
        for tid, p in agent.tabs.items():
            if p is page and not p.is_closed():
                return tid
        tid = secrets.token_hex(16)
        agent.tabs[tid] = page
        return tid

    def _page(self, agent, tid):
        if tid:
            page = agent.tabs.get(tid)
            if not page or page.is_closed():
                raise BrowserError("TAB_NOT_FOUND", "No tab with this ID in owned session", 404)
            return page
        for page in agent.tabs.values():
            if not page.is_closed():
                return page
        raise BrowserError("TAB_NOT_FOUND", "Browser session has no tabs", 404)

    async def _tabs(self, agent):
        out = []
        for tid, p in list(agent.tabs.items()):
            if p.is_closed():
                agent.tabs.pop(tid, None)
            else:
                out.append({"tab_id": tid, "url": p.url, "title": await p.title()})
        return out

    async def _page_info(self, agent, page):
        return {
            "tab_id": self._register(agent, page),
            "url": page.url,
            "title": await page.title(),
            "tabs": await self._tabs(agent),
            "effective_mode": self._mode(agent.requested_mode),
        }

    def info(self, sid, token):
        row = self._authorized(sid, token)
        return {
            "session_id": sid,
            "persist_profile": bool(row["persistent"]),
            "requested_mode": row["mode"],
            "effective_mode": self._mode(row["mode"]),
            "state": row["state"],
            "connected": sid in self.sessions,
        }

    def list(self, tokens):
        result = []
        for token in tokens[:100]:
            m = TOKEN.fullmatch(token) if isinstance(token, str) else None
            if m:
                try:
                    result.append(self.info(m.group(1), token))
                except BrowserError:
                    pass
        return {"data": result}

    async def tabs(self, sid, token):
        agent = self._active(sid, token)
        async with agent.lock:
            return {
                "tabs": await self._tabs(agent),
                "effective_mode": self._mode(agent.requested_mode),
            }

    async def snapshot(
        self, sid, token, *, tab_id="", selector="", kind="accessibility", max_chars=12000
    ):
        agent = self._active(sid, token)
        async with agent.lock:
            page = self._page(agent, tab_id)
            locator = page.locator(selector or "body")
            if kind == "accessibility":
                result = await locator.aria_snapshot(timeout=15000)
            elif kind == "text":
                result = await locator.inner_text(timeout=15000)
            elif kind == "html":
                result = await locator.inner_html(timeout=15000)
            else:
                raise BrowserError("INVALID_ARGUMENT", "Unknown snapshot kind")
            max_chars = max(100, min(200000, int(max_chars)))
            return {
                **(await self._page_info(agent, page)),
                "snapshot": result[:max_chars],
                "truncated": len(result) > max_chars,
                "kind": kind,
            }

    def _save_file(self, sid, content, filename, mime):
        if len(content) > FILE_LIMIT:
            raise BrowserError("FILE_TOO_LARGE", "Binary exceeds maximum 70 MiB", 413)
        file_id = secrets.token_hex(16)
        folder = self.root / "files" / sid
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (folder / file_id).open("xb") as f:
            os.chmod(folder / file_id, 0o600)
            f.write(content)
        with self.db:
            self.db.execute(
                "INSERT INTO files VALUES(?,?,?,?,?,?)",
                (file_id, sid, filename[:240], mime, len(content), time.time()),
            )
        return {
            "file_id": file_id,
            "mime_type": mime,
            "size_bytes": len(content),
            "filename": filename[:240],
            "url": f"/v1/browser/sessions/{sid}/files/{file_id}",
        }

    def upload(self, sid, token, content, filename, mime):
        self._authorized(sid, token)
        return self._save_file(sid, content, filename, mime)

    def file(self, sid, token, fid):
        self._authorized(sid, token)
        row = (
            self.db.execute("SELECT * FROM files WHERE sid=? AND file_id=?", (sid, fid)).fetchone()
            if SID.fullmatch(fid)
            else None
        )
        path = self.root / "files" / sid / fid
        if not row or not path.is_file():
            raise BrowserError("FILE_NOT_FOUND", "File absent or not owned by session", 404)
        return dict(row), path

    async def screenshot(
        self, sid, token, *, tab_id="", selector="", full_page=False, quality=70, format="jpeg"
    ):
        agent = self._active(sid, token)
        if format not in ("jpeg", "png", "webp"):
            raise BrowserError("INVALID_ARGUMENT", "Screenshot format must be jpeg/png/webp")
        async with agent.lock:
            page = self._page(agent, tab_id)
            kw = {"type": format, "timeout": 30000}
            if format != "png":
                kw["quality"] = max(1, min(100, int(quality)))
            if selector:
                blob = await page.locator(selector).screenshot(**kw)
            else:
                blob = await page.screenshot(full_page=bool(full_page), **kw)
            return {
                **self._save_file(sid, blob, f"screenshot.{format}", f"image/{format}"),
                "tab_id": self._register(agent, page),
                "effective_mode": self._mode(agent.requested_mode),
            }

    async def _apply(self, agent, action, data):
        tid = data.get("tab_id", "")
        timeout = max(100, min(90000, int(data.get("timeout_ms", 15000))))
        if action == "new_tab":
            return await self._page_info(agent, await self._new_page(agent))
        page = self._page(agent, tid)
        if action == "close_tab":
            await page.close()
            return {
                "closed": True,
                "tabs": await self._tabs(agent),
                "effective_mode": self._mode(agent.requested_mode),
            }
        selector = data.get("selector", "")
        loc = page.locator(selector) if selector else None
        if action == "navigate":
            url = data.get("url")
            if not isinstance(url, str) or not url.startswith(
                ("http://", "https://", "data:", "about:blank")
            ):
                raise BrowserError("INVALID_URL", "Invalid navigation URL")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        elif action == "click":
            await loc.click(timeout=timeout)
        elif action == "fill":
            await loc.fill(str(data.get("text", "")), timeout=timeout)
        elif action == "press":
            await loc.press(str(data.get("text", "")), timeout=timeout)
        elif action == "scroll":
            await page.mouse.wheel(int(data.get("dx", 0)), int(data.get("dy", 0)))
        elif action == "select":
            selected = await loc.select_option(data.get("value"), timeout=timeout)
            return {**(await self._page_info(agent, page)), "selected": selected}
        elif action == "hover":
            await loc.hover(timeout=timeout)
        elif action == "wait_for":
            await loc.wait_for(state=data.get("state", "visible"), timeout=timeout)
        elif action == "evaluate":
            expr = data.get("expression")
            if not isinstance(expr, str):
                raise BrowserError("INVALID_ARGUMENT", "JavaScript expression required")
            result = await page.evaluate(expr, data.get("arg"))
            try:
                encoded = json.dumps(result, ensure_ascii=False)
            except (ValueError, TypeError):
                raise BrowserError("INVALID_RESULT", "Non-JSON JavaScript result")
            if len(encoded.encode()) > 4 * 1024 * 1024:
                raise BrowserError("RESULT_TOO_LARGE", "JS result exceeds 4 MiB", 413)
            return {**(await self._page_info(agent, page)), "value": result}
        elif action == "upload":
            fid = data.get("file_id", "")
            _, path = self.file(agent.sid, data["session_token"], fid)
            await loc.set_input_files(str(path), timeout=timeout)
        elif action == "download":
            async with page.expect_download(timeout=timeout) as event:
                await loc.click(timeout=timeout)
            download = await event.value
            filename = download.suggested_filename
            path = self.root / "files" / (".incoming-" + secrets.token_hex(12))
            await download.save_as(path)
            try:
                blob = path.read_bytes()
            finally:
                path.unlink(missing_ok=True)
            return {
                **(await self._page_info(agent, page)),
                "file": self._save_file(agent.sid, blob, filename, "application/octet-stream"),
            }
        else:
            raise BrowserError("UNKNOWN_ACTION", "Unsupported browser action")
        return await self._page_info(agent, page)

    async def action(self, sid, token, body):
        agent = self._active(sid, token)
        if not isinstance(body, dict):
            raise BrowserError("INVALID_ARGUMENT", "Action object required")
        act = body.get("action")
        if act not in ACTIONS:
            raise BrowserError("UNKNOWN_ACTION", "Action not supported")
        mode = body.get("mode")
        permitted_modes = {None, "auto", agent.requested_mode}
        if agent.requested_mode != "foreground":
            permitted_modes.update(("background", "visible_background"))
            if agent.requested_mode == "headless":
                permitted_modes.add("headless")
        if mode not in permitted_modes:
            raise BrowserError("REQUIRES_FOREGROUND", "Cannot change focus mode during action", 409)
        key = body.get("client_request_id")
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise BrowserError("IDEMPOTENCY_REQUIRED", "client_request_id required (8..180 chars)")
        payload = {k: v for k, v in body.items() if k != "session_token"}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        async with agent.lock:
            prior = self.db.execute(
                "SELECT * FROM operations WHERE sid=? AND request_key=?", (sid, key)
            ).fetchone()
            if prior:
                if prior["payload_sha"] != digest:
                    raise BrowserError(
                        "IDEMPOTENCY_CONFLICT", "Request key reused with different arguments", 409
                    )
                return self._record(prior, replayed=True)
            oid = str(uuid.uuid4())
            now = time.time()
            with self.db:
                self.db.execute(
                    "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (oid, sid, key, act, digest, "running", None, None, now, now),
                )
            try:
                result = await self._apply(agent, act, {**body, "session_token": token})
                state = "completed"
                error = None
            except BrowserError as exc:
                result = None
                state = "failed"
                error = exc.code
            except (PlaywrightTimeout, asyncio.TimeoutError, ConnectionError, OSError):
                result = None
                state = "unknown"
                error = "OUTCOME_UNCERTAIN"
            except asyncio.CancelledError:
                with self.db:
                    self.db.execute(
                        "UPDATE operations SET state='unknown',error_code='CANCELLED_UNKNOWN',updated=? WHERE operation_id=?",
                        (time.time(), oid),
                    )
                raise
            except Exception as exc:
                result = None
                state = "unknown"
                error = type(exc).__name__
            with self.db:
                self.db.execute(
                    "UPDATE operations SET state=?,result=?,error_code=?,updated=? WHERE operation_id=?",
                    (
                        state,
                        json.dumps(result, ensure_ascii=False) if result is not None else None,
                        error,
                        time.time(),
                        oid,
                    ),
                )
            saved = self.db.execute(
                "SELECT * FROM operations WHERE operation_id=?", (oid,)
            ).fetchone()
            return self._record(saved)

    @staticmethod
    def _record(row, replayed=False):
        return {
            "operation_id": row["operation_id"],
            "session_id": row["sid"],
            "action": row["action"],
            "state": row["state"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error_code": row["error_code"],
            "replayed": replayed,
        }

    def operation_by_key(self, sid, token, key):
        """Read-only idempotency lookup after ambiguous HTTP disconnect."""
        self._authorized(sid, token)
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise BrowserError("INVALID_ARGUMENT", "Invalid client_request_id")
        row = self.db.execute(
            "SELECT * FROM operations WHERE sid=? AND request_key=?", (sid, key)
        ).fetchone()
        if not row:
            raise BrowserError("OPERATION_NOT_FOUND", "Operation key not found", 404)
        return self._record(row)

    def operation(self, sid, token, oid):
        self._authorized(sid, token)
        row = self.db.execute(
            "SELECT * FROM operations WHERE sid=? AND operation_id=?", (sid, oid)
        ).fetchone()
        if not row:
            raise BrowserError("OPERATION_NOT_FOUND", "Operation not found", 404)
        return self._record(row)

    async def close(self, sid, token):
        row = self._authorized(sid, token)
        async with self.lifecycle:
            agent = self.sessions.pop(sid, None)
            if agent:
                async with agent.lock:
                    await self._close_context(agent)
            with self.db:
                self.db.execute(
                    "UPDATE sessions SET state='closed',updated=? WHERE sid=?", (time.time(), sid)
                )
            if not row["persistent"]:
                shutil.rmtree(self.root / "files" / sid, ignore_errors=True)
                shutil.rmtree(self.root / "profiles" / sid, ignore_errors=True)
                with self.db:
                    self.db.execute("DELETE FROM files WHERE sid=?", (sid,))
        return {"session_id": sid, "closed": True, "persist_profile": bool(row["persistent"])}

    async def reopen(self, sid, token):
        row = self._authorized(sid, token)
        if not row["persistent"]:
            raise BrowserError("NOT_PERSISTENT", "Cannot reopen temporary session", 409)
        if sid in self.sessions:
            return {**self.info(sid, token), "reopened": False}
        async with self.lifecycle:
            ctx = await self._context(sid, True, row["mode"])
            agent = Session(sid, True, row["mode"], ctx)
            self.sessions[sid] = agent
            for page in ctx.pages:
                self._register(agent, page)
            if not agent.tabs:
                self._register(agent, await self._new_page(agent))
            with self.db:
                self.db.execute(
                    "UPDATE sessions SET state='open',updated=? WHERE sid=?", (time.time(), sid)
                )
            return {**self.info(sid, token), "reopened": True, "tabs": await self._tabs(agent)}

    async def shutdown(self):
        for sid, agent in list(self.sessions.items()):
            try:
                await self._close_context(agent)
            except Exception:
                pass
            self.sessions.pop(sid, None)
        if self.shared:
            try:
                await self.shared.close()
            except Exception:
                pass
        if self.pw:
            try:
                await self.pw.stop()
            except Exception:
                pass
        self.db.close()
