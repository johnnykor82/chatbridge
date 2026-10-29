"""Versioned HTTP endpoints for ChatBridge-owned headless agent browsers."""

from __future__ import annotations

from aiohttp import web

from .background_browser import BackgroundBrowsers, BrowserError

MAX_UPLOAD = 70 * 1024 * 1024


def install_browser_routes(app: web.Application, manager: BackgroundBrowsers):
    def capability(req):
        return req.headers.get("X-Browser-Session-Token", "")

    def sid(req):
        return req.match_info["session_id"]

    @web.middleware
    async def browser_errors(req, handler):
        try:
            return await handler(req)
        except BrowserError as exc:
            return web.json_response(
                {"error": {"code": exc.code, "message": str(exc)}}, status=exc.status
            )
        except (ValueError, TypeError, OverflowError):
            return web.json_response(
                {"error": {"code": "INVALID_ARGUMENT", "message": "Invalid browser parameters"}},
                status=400,
            )

    # Insert nested middleware before general auth routing; general API bearer
    # middleware is still applied to every browser route.
    app.middlewares.append(browser_errors)

    async def capabilities(req):
        return web.json_response(
            {
                "api_version": "1",
                "browser_owner": "ChatBridge",
                "modes": {
                    "background": "visible_background",
                    "headless": "headless",
                    "visible_background": "visible_background",
                    "foreground": "explicit_only_separate_persistent_profile",
                },
                "actions": sorted(
                    __import__("chatbridge.background_browser", fromlist=["ACTIONS"]).ACTIONS
                ),
                "independent_from_chatgpt_sessions": True,
                "max_upload_bytes": MAX_UPLOAD,
                "screenshots": "binary_file_urls",
                "session_capability_header": "X-Browser-Session-Token",
                "idempotency": "client_request_id required; unknown never automatically replayed",
                "focus_policy": "No macOS global mouse/keyboard, no window activation",
            }
        )

    async def create(req):
        data = await req.json()
        if not isinstance(data, dict):
            raise BrowserError("INVALID_ARGUMENT", "JSON object required")
        result = await manager.create(
            persistent=data.get("persist_profile", False),
            mode=data.get("mode", "background"),
            url=data.get("url", "about:blank"),
        )
        return web.json_response(result, status=201)

    async def list_sessions(req):
        data = await req.json() if req.can_read_body else {}
        if not req.can_read_body and capability(req):
            data = {"session_tokens": [capability(req)]}
        if not isinstance(data, dict) or not isinstance(data.get("session_tokens", []), list):
            raise BrowserError("INVALID_ARGUMENT", "session_tokens list required")
        return web.json_response(manager.list(data.get("session_tokens", [])))

    async def info(req):
        return web.json_response(manager.info(sid(req), capability(req)))

    async def close(req):
        return web.json_response(await manager.close(sid(req), capability(req)))

    async def reopen(req):
        return web.json_response(await manager.reopen(sid(req), capability(req)))

    async def tabs(req):
        return web.json_response(await manager.tabs(sid(req), capability(req)))

    async def new_tab(req):
        body = await req.json() if req.can_read_body else {}
        if not isinstance(body, dict):
            raise BrowserError("INVALID_ARGUMENT", "Object required")
        result = await manager.action(sid(req), capability(req), {**body, "action": "new_tab"})
        return web.json_response(result, status=200 if result["state"] == "completed" else 202)

    async def action(req):
        body = await req.json()
        if not isinstance(body, dict):
            raise BrowserError("INVALID_ARGUMENT", "Object required")
        result = await manager.action(sid(req), capability(req), body)
        return web.json_response(result, status=200 if result["state"] == "completed" else 202)

    async def operation_by_key(req):
        key = req.query.get("client_request_id", "")
        return web.json_response(manager.operation_by_key(sid(req), capability(req), key))

    async def operation(req):
        return web.json_response(
            manager.operation(sid(req), capability(req), req.match_info["operation_id"])
        )

    async def snapshot(req):
        return web.json_response(
            await manager.snapshot(
                sid(req),
                capability(req),
                tab_id=req.query.get("tab_id", ""),
                selector=req.query.get("selector", ""),
                kind=req.query.get("kind", "accessibility"),
                max_chars=int(req.query.get("max_chars", "12000")),
            )
        )

    async def screenshot(req):
        return web.json_response(
            await manager.screenshot(
                sid(req),
                capability(req),
                tab_id=req.query.get("tab_id", ""),
                selector=req.query.get("selector", ""),
                full_page=req.query.get("full_page", "false").lower() == "true",
                quality=int(req.query.get("quality", "70")),
                format=req.query.get("format", "jpeg"),
            )
        )

    async def file_upload(req):
        # Raw bytes via POST; avoids huge base64 JSON and allows unmodified media.
        raw = await req.read()
        if len(raw) > MAX_UPLOAD:
            raise BrowserError("FILE_TOO_LARGE", "Upload exceeds 70 MiB", 413)
        name = req.headers.get("X-Filename", "upload.bin")
        mime = req.headers.get("Content-Type", "application/octet-stream")
        return web.json_response(
            manager.upload(sid(req), capability(req), raw, name, mime), status=201
        )

    async def file_fetch(req):
        row, path = manager.file(sid(req), capability(req), req.match_info["file_id"])
        return web.FileResponse(
            path,
            headers={
                "Content-Type": row["mime"],
                "Cache-Control": "private, no-store",
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + __import__("urllib.parse", fromlist=["quote"]).quote(row["filename"]),
                "X-Content-Type-Options": "nosniff",
            },
        )

    app.router.add_get("/v1/browser/capabilities", capabilities)
    app.router.add_post("/v1/browser/sessions", create)
    app.router.add_post("/v1/browser/sessions/list", list_sessions)
    app.router.add_get("/v1/browser/sessions", list_sessions)
    app.router.add_get("/v1/browser/sessions/{session_id}", info)
    app.router.add_delete("/v1/browser/sessions/{session_id}", close)
    app.router.add_post("/v1/browser/sessions/{session_id}/reopen", reopen)
    app.router.add_get("/v1/browser/sessions/{session_id}/tabs", tabs)
    app.router.add_post("/v1/browser/sessions/{session_id}/tabs", new_tab)
    app.router.add_post("/v1/browser/sessions/{session_id}/actions", action)
    app.router.add_get("/v1/browser/sessions/{session_id}/operations", operation_by_key)
    app.router.add_get("/v1/browser/sessions/{session_id}/operations/{operation_id}", operation)
    app.router.add_get("/v1/browser/sessions/{session_id}/snapshot", snapshot)
    app.router.add_get("/v1/browser/sessions/{session_id}/screenshot", screenshot)
    app.router.add_post("/v1/browser/sessions/{session_id}/files", file_upload)
    app.router.add_get("/v1/browser/sessions/{session_id}/files/{file_id}", file_fetch)
