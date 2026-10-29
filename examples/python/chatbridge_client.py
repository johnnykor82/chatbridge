"""Minimal standard-library ChatBridge client (HTTP JSON, no provider SDK).

Use stable client_request_id on writes. An ambiguous transport failure MUST
NOT be retried with a different key. Projects are local unless verified.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ChatBridgeError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(message)


class OutcomeUnknown(RuntimeError):
    """A write may have reached ChatBridge; reconcile before taking new action."""

    def __init__(self, operation: str, client_request_id: str):
        self.operation = operation
        self.client_request_id = client_request_id
        super().__init__(
            f"{operation} result unavailable; inspect local state before retrying; "
            f"client_request_id={client_request_id}"
        )


class ChatBridgeClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8788",
                 api_key: str = "", timeout: float = 30):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Absolute HTTP(S) base_url required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body=None,
                 *, request_key: str = "") -> dict:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        raw = None
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base_url + path, data=raw, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            payload = {}
            try:
                payload = json.loads(error.read(4096))
            except (ValueError, UnicodeDecodeError):
                pass
            details = payload.get("error", {})
            if not isinstance(details, dict):
                details = {}
            code = str(details.get("code") or f"HTTP_{error.code}")
            message = str(details.get("message") or f"HTTP {error.code}")
            raise ChatBridgeError(error.code, code, message) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                raise OutcomeUnknown(method + " " + path, request_key) from None
            raise

    @staticmethod
    def request_key() -> str:
        return "cb-" + uuid.uuid4().hex

    def create_project(self, name: str, *, memory_mode: str = "default",
                       client_request_id: str) -> dict:
        if memory_mode not in ("default", "project_only"):
            raise ValueError("Invalid memory_mode")
        if not client_request_id:
            raise ValueError("Stable client_request_id required")
        return self._request("POST", "/v1/projects", {
            "name": name, "memory_mode": memory_mode,
            "client_request_id": client_request_id
        }, request_key=client_request_id)

    def verify_project(self, project_id: str, name: str,
                       memory_mode: str = "default") -> dict:
        return self._request("POST", "/v1/projects/verify", {
            "project_id": project_id, "name": name, "memory_mode": memory_mode
        }, request_key="project-verification")

    def create_session(self, *, mode: str, title: str = "",
                       project_id: str | None = None) -> dict:
        if mode not in ("temporary", "continued", "persistent"):
            raise ValueError("Explicit session mode required")
        if project_id and mode == "temporary":
            raise ValueError("Temporary sessions cannot belong to projects")
        body = {"mode": mode, "title": title}
        if project_id:
            body["project_id"] = project_id
        # A disconnected session creation may have succeeded. Reconcile locally.
        return self._request("POST", "/v1/sessions", body,
                             request_key="session-create-" + mode)

    def send(self, session_id: str, content: str, *, client_request_id: str,
             reasoning_effort: str = "high") -> dict:
        if not client_request_id:
            raise ValueError("client_request_id required: never silently generate on retry")
        if reasoning_effort not in ("low", "medium", "high"):
            raise ValueError("Invalid reasoning effort")
        return self._request("POST", "/v1/sessions/" + session_id + "/messages", {
            "content": content, "client_request_id": client_request_id,
            "reasoning_effort": reasoning_effort
        }, request_key=client_request_id)

    def request(self, request_id: str) -> dict:
        return self._request("GET", "/v1/requests/" + request_id)

    def session_messages(self, session_id: str) -> dict:
        return self._request("GET", "/v1/sessions/" + session_id + "/messages")

    def reconcile(self, request_id: str) -> dict:
        """Read-only provider reconcile, NOT a second model submission."""
        return self._request("POST", "/v1/requests/" + request_id + "/reconcile",
                             {}, request_key="reconcile-" + request_id)

    def rotate(self, session_id: str) -> dict:
        """Explicitly rotate only after external wakeup/guard checks."""
        return self._request("POST", "/v1/sessions/" + session_id + "/rotate",
                             {}, request_key="rotate-" + session_id)

    def wait(self, request_id: str, *, poll_interval: float = 2,
             max_seconds: float = 180) -> dict:
        if poll_interval <= 0 or max_seconds < 0:
            raise ValueError("Invalid wait controls")
        deadline = time.monotonic() + max_seconds
        while True:
            state = self.request(request_id)
            if state.get("status") in ("completed", "failed", "unknown", "interrupted"):
                return state
            if time.monotonic() >= deadline:
                return state  # never imply completed, never resubmit
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
