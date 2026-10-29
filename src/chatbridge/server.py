"""Local aiohttp API. Durable idempotency and resumable SSE."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .cleanup_ops import (
    CleanupBlocked,
    purge_unlinked,
)
from .cleanup_ops import (
    begin as begin_cleanup,
)
from .cleanup_ops import (
    candidates as cleanup_candidates,
)
from .cleanup_ops import (
    ensure_backup as ensure_cleanup_backup,
)
from .cleanup_ops import (
    purge as purge_local_session,
)
from .cleanup_ops import (
    row as cleanup_operation,
)
from .cleanup_ops import (
    session_preview as cleanup_preview,
)
from .cleanup_ops import (
    update as update_cleanup,
)
from .config import Settings
from .engine import GatewayEngine, QueueBackpressureError
from .media import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_TOTAL_BYTES,
    MediaError,
    parse_message,
    verify_file,
)
from .project_evidence import ProjectMembershipMismatch, ProjectMembershipUnsupported
from .store import TERMINAL, DuplicatePayloadError, SessionModeError, Store

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
PROJECT_ID = re.compile(r"^g-p-[0-9a-f]{32}$", re.I)


def conversation_reference(value: str | None) -> tuple[str | None, str | None]:
    """Parse only canonical ChatGPT conversation URLs, never infer project membership."""
    if value is None or value == "":
        return None, None
    if not isinstance(value, str):
        raise ValueError("Conversation reference must be a string")
    value = value.strip()
    if UUID.fullmatch(value):
        return value, None
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("Invalid ChatGPT conversation URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc != "chatgpt.com"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Conversation URL must be a canonical chatgpt.com HTTPS URL")
    simple = re.fullmatch(r"/c/([0-9a-f-]{36})", parsed.path, re.I)
    if simple and UUID.fullmatch(simple.group(1)):
        return simple.group(1), None
    project = re.fullmatch(
        r"/g/(g-p-[0-9a-f]{32})(?:-[a-z0-9-]+)?/c/([0-9a-f-]{36})",
        parsed.path,
        re.I,
    )
    if project and UUID.fullmatch(project.group(2)):
        return project.group(2), project.group(1)
    raise ValueError("Invalid ChatGPT conversation URL or unsupported path")


def conversation_id_from(value: str | None) -> str | None:
    return conversation_reference(value)[0]


def json_error(status: int, message: str):
    return web.json_response({"error": {"message": message}}, status=status)


def make_app(store: Store, engine: GatewayEngine, settings: Settings, browser_supervisor=None, background_browsers=None):
    @web.middleware
    async def auth(request, handler):
        # Browser requests must originate from this gateway; native clients
        # without Origin retain the default keyless loopback API.
        if not settings.api_key and settings.host in {"127.0.0.1", "localhost", "::1"}:
            try:
                host = urlsplit("//" + request.host)
                local_port = request.transport.get_extra_info("sockname")[1]
                valid_host = (
                    len(request.headers.getall("Host", [])) == 1
                    and host.hostname in {"127.0.0.1", "localhost", "::1"}
                    and not host.username and not host.password
                    and (host.port or 80) == local_port
                )
            except (ValueError, TypeError):
                valid_host = False
            if not valid_host:
                return json_error(403, "Invalid gateway Host")
        origins = request.headers.getall("Origin", [])
        if origins and origins != [f"{request.scheme}://{request.host}"]:
            return json_error(403, "Cross-origin requests are not allowed")
        if settings.api_key and not secrets.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + settings.api_key
        ):
            return json_error(401, "Invalid API key")
        return await handler(request)

    app = web.Application(middlewares=[auth], client_max_size=70 * 1024 * 1024)
    if background_browsers is not None:
        from .background_http import install_browser_routes
        install_browser_routes(app,background_browsers)
    provider_checks = {"readiness_page_loads": 0, "turn_state_reads": 0}
    deletion_lock = asyncio.Lock()

    # /ready opens a real ChatGPT page. Cache success for 120s, failures
    # for 20s, invalidating when Chrome's process/endpoint changes.
    ready_cache = {"checked_at": 0.0, "browser_key": None, "ready": None}

    def cooldown_response():
        state = engine.provider_status()
        return web.json_response(
            {
                "error": {
                    "message": "ChatGPT provider cooldown active",
                    "code": "provider_rate_limited",
                },
                "rate_limit": state,
            },
            status=503,
            headers={"Retry-After": str(max(1, state["retry_after"]))},
        )

    async def resolve_chat_marker(req):
        """Read-only conversation discovery; never trusts an unverified search hit."""
        from .marker_lookup import (
            AmbiguousMarker,
            MarkerNotFound,
            canonical_candidate,
            validate_marker,
        )

        try:
            body = await req.json()
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            marker = validate_marker(body.get("marker"))
        except (ValueError, TypeError, web.HTTPException) as exc:
            return json_error(400, str(exc)[:180])
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        if not hasattr(engine.transport, "resolve_chat_marker"):
            return json_error(501, "Marker search unsupported by selected transport")
        try:
            result = await engine.transport.resolve_chat_marker(marker)
        except MarkerNotFound as exc:
            return json_error(404, str(exc)[:160])
        except AmbiguousMarker:
            return json_error(409, "Marker does not uniquely identify one conversation")
        except Exception as exc:
            logging.getLogger(__name__).warning("Marker lookup failed: %s", str(exc)[:400])
            if engine.provider_status()["provider_rate_limited"]:
                return cooldown_response()
            return json_error(502, "Marker lookup failed: " + type(exc).__name__)
        # Never pass a candidate URL through without a verified user message.
        if (result.get("evidence_verified") is not True
                or result.get("verification") not in ("user_message", "assistant_message")
                or result.get("matches") != 1
                or canonical_candidate(result.get("chat_url")) != result.get("chat_url")):
            return json_error(502, "Marker evidence or canonical chat URL not verified")
        return web.json_response(result)

    async def create_project(req):
        try:
            data = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        if not isinstance(data, dict):
            return json_error(400, "Expected JSON object")
        name = data.get("name")
        memory_mode = data.get("memory_mode", "default")
        key = data.get("client_request_id")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            return json_error(400, "Project name must contain 1..120 characters")
        if memory_mode not in ("default", "project_only"):
            return json_error(400, "memory_mode must be default or project_only")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 200):
            return json_error(400, "Invalid client_request_id")
        if not hasattr(engine.transport, "create_project"):
            return json_error(501, "Project creation unsupported by selected transport")
        project_key = store.project_request_key(name, memory_mode, key)
        if (
            engine.provider_status()["provider_rate_limited"]
            and store.get_project_operation_by_key(project_key) is None
        ):
            return cooldown_response()
        try:
            operation, created = store.begin_project_creation(name, memory_mode, key)
        except DuplicatePayloadError:
            return json_error(409, "client_request_id reused with different project settings")
        if not created:
            if operation["state"] == "completed":
                return web.json_response(
                    store.get_project(operation["project_id"]),
                    status=200,
                    headers={"X-ChatBridge-Operation-ID": operation["operation_id"]},
                )
            return web.json_response(
                {
                    "error": {
                        "message": "Project creation pending or outcome unknown; "
                        "reconcile the existing project before attempting another creation",
                        "code": "project_creation_" + operation["state"],
                    },
                    "operation_id": operation["operation_id"],
                    "client_request_id": project_key,
                    "state": operation["state"],
                },
                status=202 if operation["state"] == "creating" else 409,
            )
        try:
            verified_snapshot = False
            project_id = await engine.transport.create_project(name.strip(), memory_mode)
            if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
                raise RuntimeError("ChatGPT did not confirm a stable project ID")
            if hasattr(engine.transport, "inspect_project"):
                verified = await engine.transport.inspect_project(project_id)
                if (
                    verified.get("project_id") != project_id
                    or verified.get("name") != name.strip()
                    or verified.get("memory_mode") != memory_mode
                ):
                    raise RuntimeError("ChatGPT project name or memory mode differs from request")
                verified_snapshot = True
            row = store.complete_project_creation(operation["operation_id"], project_id)
            if verified_snapshot:
                store.record_project_verification(project_id, name.strip(), memory_mode)
        except asyncio.CancelledError:
            store.mark_project_creation_unknown(operation["operation_id"], "client disconnected")
            raise
        except Exception as exc:
            store.mark_project_creation_unknown(operation["operation_id"], type(exc).__name__)
            return web.json_response(
                {
                    "error": {
                        "message": "ChatGPT project creation outcome unconfirmed; "
                        "do not repeat creation blindly",
                        "code": "project_creation_unknown",
                    },
                    "operation_id": operation["operation_id"],
                    "state": "unknown",
                },
                status=502,
            )
        return web.json_response(
            row,
            status=201,
            headers={"X-ChatBridge-Operation-ID": operation["operation_id"]},
        )

    async def get_project_creation(req):
        row = store.get_project_operation(req.match_info["operation_id"])
        if not row:
            return json_error(404, "Unknown project creation operation")
        return web.json_response(row)

    async def verify_existing_project(req):
        """Import an existing ChatGPT project, or resolve an uncertain creation."""
        try:
            data = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        if not isinstance(data, dict):
            return json_error(400, "Expected JSON object")
        pid = data.get("project_id")
        opid = data.get("operation_id")
        if not isinstance(pid, str) or not PROJECT_ID.fullmatch(pid):
            return json_error(400, "Invalid project_id")
        if opid is not None and (not isinstance(opid, str) or not UUID.fullmatch(opid)):
            return json_error(400, "Invalid operation_id")
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        if not hasattr(engine.transport, "inspect_project"):
            return json_error(501, "Verified project import unsupported by transport")
        operation = store.get_project_operation(opid) if opid else None
        if opid and not operation:
            return json_error(404, "Unknown project creation operation")
        if operation and operation["state"] == "creating":
            return json_error(409, "Cannot resolve an in-progress project creation")
        if operation and operation["state"] == "completed" and operation["project_id"] != pid:
            return json_error(409, "Creation operation already bound to another project")
        try:
            verified = await engine.transport.inspect_project(pid)
            if verified.get("project_id") != pid:
                return json_error(502, "Project identity could not be verified")
            name, memory = verified.get("name"), verified.get("memory_mode")
            if (
                not isinstance(name, str)
                or not name.strip()
                or memory not in ("default", "project_only")
            ):
                return json_error(502, "Project name/memory verification incomplete")
            for field, actual in (("name", name), ("memory_mode", memory)):
                if field in data and data[field] != actual:
                    return json_error(409, "Remote project " + field + " differs")
            if operation and (operation["name"] != name or operation["memory_mode"] != memory):
                return json_error(409, "Unknown operation does not match verified remote project")
            old = store.get_project(pid)
            if operation and operation["state"] == "unknown":
                row = store.complete_project_creation(opid, pid)
            else:
                row = store.save_project(pid, name, memory)
            store.record_project_verification(pid, name, memory)
        except Exception as exc:
            return json_error(
                502, "Read-only project verification unavailable: " + type(exc).__name__
            )
        return web.json_response(row, status=200 if old else 201)

    async def list_projects(req):
        scope = req.query.get("scope", "local")
        if scope == "remote":
            return web.json_response({
                "error": {"code": "not_supported",
                          "message": "Full provider project enumeration is not verified"},
            }, status=501)
        if scope != "local":
            return json_error(400, "scope must be local or remote")
        return web.json_response({
            "data": store.list_projects(), "scope": "locally_registered_projects_only",
            "freshness": "snapshot_not_live",
        })

    async def get_project(req):
        row = store.get_project(req.match_info["project_id"])
        if row is None:
            return json_error(404, "Unknown project")
        return web.json_response(row)

    async def deletion_preview(req):
        info = cleanup_preview(store.db, req.match_info["session_id"])
        if info is None:
            return json_error(404, "Unknown session")
        return web.json_response(info)

    async def cleanup_plan(req):
        """Read-only local classification, not a batch delete command."""
        try:
            body = await req.json()
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            title_prefix = body.get("title_prefix")
            limit = body.get("limit", 100)
            info = cleanup_candidates(store.db, title_prefix, limit)
        except (CleanupBlocked, ValueError, TypeError, web.HTTPException) as exc:
            return json_error(400, str(exc)[:160])
        return web.json_response({"data": info, "dry_run": True,
                                  "eligible": sum(bool(x["safe_candidate"] and x["conversation_id"]) for x in info),
                                  "requires_per_session_confirmation": True})

    async def deletion_operation(req):
        op = cleanup_operation(store.db, req.match_info["operation_id"])
        if not op:
            return json_error(404, "Unknown deletion operation")
        return web.json_response(op)

    async def delete_remote_conversation(req):
        sid = req.match_info["session_id"]
        try:
            body = await req.json()
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            if body.get("confirm") != "delete_remote":
                return json_error(400, "confirm=delete_remote is required")
            key, cid, title = (body.get("client_request_id"),
                               body.get("expected_conversation_id"),
                               body.get("expected_title"))
        except (TypeError, ValueError, web.HTTPException):
            return json_error(400, "Expected JSON object")
        async with deletion_lock:
            # Replays of a previously registered key work without provider access.
            try:
                prior = store.db.execute(
                    "SELECT * FROM conversation_delete_ops WHERE client_request_id=?", (key,)
                ).fetchone() if isinstance(key, str) else None
                if prior is None:
                    if engine.active or engine.provider_status()["provider_rate_limited"]:
                        return cooldown_response() if engine.provider_status()["provider_rate_limited"] else json_error(
                            409, "Gateway has active model work; retry later")
                op, created = begin_cleanup(store.db, sid, key, cid, title)
            except KeyError:
                return json_error(404, "Unknown session")
            except (CleanupBlocked, TypeError, ValueError) as exc:
                return json_error(409, str(exc)[:170])
            if not created:
                return web.json_response(op, status=200 if op["state"] in ("remote_deleted","purged") else 202)
            if not hasattr(engine.transport, "delete_conversation"):
                op = update_cleanup(store.db, op["operation_id"], "unknown",
                                    "Transport does not support deletion")
                return web.json_response(op, status=501)
            try:
                verified = await engine.transport.delete_conversation(cid)
                op = update_cleanup(store.db, op["operation_id"],
                                    "remote_deleted" if verified is True else "unknown",
                                    None if verified is True else "provider did not confirm deletion")
            except asyncio.CancelledError:
                update_cleanup(store.db, op["operation_id"], "unknown", "client disconnected")
                raise
            except Exception as exc:
                op = update_cleanup(store.db, op["operation_id"], "unknown",
                                    "provider request uncertain: " + type(exc).__name__)
            return web.json_response(op, status=200 if op["state"]=="remote_deleted" else 202)

    async def reconcile_deletion(req):
        oid = req.match_info["operation_id"]
        async with deletion_lock:
            op = cleanup_operation(store.db, oid)
            if not op:
                return json_error(404, "Unknown deletion operation")
            if op["state"] in ("remote_deleted","purged"):
                return web.json_response(op)
            if op["state"] != "unknown":
                return json_error(409, "Remote deletion is in progress")
            if engine.active:
                return json_error(409, "Gateway busy")
            if engine.provider_status()["provider_rate_limited"]:
                return cooldown_response()
            if not hasattr(engine.transport, "verify_conversation_deleted"):
                return json_error(501, "Read-only remote deletion verification unsupported")
            try:
                gone = await engine.transport.verify_conversation_deleted(op["conversation_id"])
            except Exception as exc:
                return web.json_response(update_cleanup(
                    store.db, oid, "unknown", "verification unavailable: "+type(exc).__name__),status=202)
            if gone is True:
                return web.json_response(update_cleanup(store.db, oid, "remote_deleted"))
            return web.json_response(update_cleanup(
                store.db, oid, "unknown", "ChatGPT still reports visible conversation"),status=202)

    async def delete_local_session(req):
        sid = req.match_info["session_id"]
        oid = req.query.get("operation_id", "")
        unlinked = req.headers.get("X-Confirm-Purge") == "purge-unlinked"
        if not unlinked and req.headers.get("X-Confirm-Purge") != "purge-local":
            return json_error(400, "Explicit X-Confirm-Purge header required")
        if not unlinked and not UUID.fullmatch(oid):
            return json_error(400, "Valid operation_id required")
        if unlinked:
            try:
                data = await req.json()
                if not isinstance(data, dict) or data.get("confirm") != "purge_unlinked":
                    return json_error(400, "confirm=purge_unlinked required")
            except (TypeError, ValueError, web.HTTPException):
                return json_error(400, "Valid JSON body required")
        async with deletion_lock:
            if engine.active:
                return json_error(409, "Gateway busy")
            try:
                # An online SQLite backup is mandatory before local mutation.
                ensure_cleanup_backup(store.db,store.path)
                result = (purge_unlinked(store.db,sid,
                          expected_title=data.get("expected_title"),
                          client_request_id=data.get("client_request_id"))
                          if unlinked else purge_local_session(store.db, sid, oid))
            except KeyError:
                return json_error(404, "Unknown session")
            except CleanupBlocked as exc:
                return json_error(409, str(exc)[:160])
            return web.json_response({"session_id":sid, "deleted":True, "operation":result})

    def require_known_project(req):
        pid = req.match_info["project_id"]
        if not PROJECT_ID.fullmatch(pid):
            return None, json_error(400, "Invalid project_id")
        project = store.get_project(pid)
        if project is None:
            return None, json_error(404, "Unknown project")
        return project, None

    def not_supported(pid: str, message: str):
        return web.json_response({
            "error": {"code": "not_supported", "message": message}, "project_id": pid,
        }, status=501)

    async def project_metadata(req):
        """Local-only, paginated project/session records with field-level evidence."""
        project, error = require_known_project(req)
        if error is not None:
            return error
        pid = project["project_id"]
        try:
            limit = int(req.query.get("limit", "1000"))
            offset = int(req.query.get("offset", "0"))
            if not 1 <= limit <= 1000 or not 0 <= offset <= 10000000:
                raise ValueError("Invalid pagination bounds")
        except (ValueError, TypeError):
            return json_error(400, "limit must be 1..1000 and offset 0..10000000")
        sessions = store.list_sessions(project_id=pid, limit=limit, offset=offset)
        total = store.count_project_sessions(pid)
        verification = store.get_project_verification(pid)
        evidence = ({"source": verification["source"],
                     "observed_at": verification["observed_at"],
                     "freshness": "historical_snapshot"}
                    if verification and (
                        verification["name"], verification["memory_mode"]
                    ) == (project["name"], project["memory_mode"])
                    else {"source": "local_record_no_ui_evidence",
                          "observed_at": None, "freshness": "unverified"})
        chats = []
        for row in sessions:
            v = store.get_session_project_verification(
                row["session_id"], row["conversation_id"]
            )
            corroborated = bool(v and v["project_id"] == pid)
            membership = (
                {"source": v["source"], "observed_at": v["observed_at"],
                 "freshness": "historical_snapshot"}
                if corroborated
                else {"source": "local_session_binding", "observed_at": None,
                      "freshness": "unverified"}
            )
            chats.append({
                "session_id": row["session_id"],
                "conversation_id": row["conversation_id"],
                "project_id": pid,
                "membership": ("provider_reported_at_import_not_live" if corroborated
                               else "locally_bound_not_live_verified"),
                "membership_evidence": membership,
            })
        return web.json_response({
            "project_id": pid, "name": project["name"],
            "memory_mode": project["memory_mode"],
            "provenance": ("ui_verified_local_snapshot" if verification
                           and evidence["source"] == "ui_project_settings_dialog"
                           else "local_record_without_ui_evidence"),
            "freshness": "snapshot_not_live", "field_evidence": {
                "name": evidence, "memory_mode": evidence,
            },
            "chat_membership_scope": "locally_bound_sessions_only",
            "chats": chats,
            "chats_total_local": total, "limit": limit, "offset": offset,
            "chats_truncated": offset + len(chats) < total,
            "project_sources": {"status": "not_supported"},
            "project_instructions": {"status": "not_supported"},
        })

    async def live_project_metadata(req):
        """Read-only UI settings snapshot; never mutates the remote or local project."""
        project, error = require_known_project(req)
        if error is not None:
            return error
        pid = project["project_id"]
        if not hasattr(engine.transport, "inspect_project"):
            return not_supported(pid, "Project settings inspection unavailable")
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        try:
            verified = await engine.transport.inspect_project(pid)
        except NotImplementedError:
            return not_supported(pid, "Project settings inspection unavailable")
        except Exception as exc:
            return json_error(502, "Project settings read failed: " + type(exc).__name__)
        if not isinstance(verified, dict) or (
            verified.get("project_id") != pid
            or not isinstance(verified.get("name"), str)
            or not verified["name"].strip()
            or verified.get("memory_mode") not in ("default", "project_only")
        ):
            return json_error(502, "Incomplete project settings verification")
        observed_at = time.time()
        evidence = {"source": "ui_project_settings_dialog", "observed_at": observed_at,
                    "freshness": "point_in_time_snapshot"}
        return web.json_response({
            "project_id": pid, "name": verified["name"],
            "memory_mode": verified["memory_mode"],
            "field_evidence": {"name": evidence, "memory_mode": evidence},
            "freshness": "point_in_time_read_only", "saved_locally": False,
        })

    async def verify_chat_membership(req):
        """Read-only membership verification requires backend project ID evidence."""
        project, error = require_known_project(req)
        if error is not None:
            return error
        pid = project["project_id"]
        cid = req.match_info["conversation_id"]
        if not UUID.fullmatch(cid):
            return json_error(400, "Invalid conversation_id")
        if not hasattr(engine.transport, "inspect_conversation"):
            return not_supported(pid, "Verified conversation membership unsupported")
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        try:
            proof = await engine.transport.inspect_conversation(cid, project_id=pid)
        except ProjectMembershipUnsupported:
            return not_supported(pid, "Provider project membership evidence unavailable")
        except NotImplementedError:
            return not_supported(pid, "Verified conversation membership unsupported")
        except ProjectMembershipMismatch:
            return json_error(409, "Provider conversation project ID disagrees")
        except Exception as exc:
            return json_error(502, "Conversation inspection failed: " + type(exc).__name__)
        if not isinstance(proof, dict) or proof.get("conversation_id") != cid:
            return json_error(502, "Conversation identity not established")
        if proof.get("project_id") != pid:
            return json_error(409, "Conversation does not belong to requested project")
        if proof.get("verification_method") != "provider_conversation_project_id":
            return not_supported(pid, "Provider-backed project ID not corroborated")
        return web.json_response({
            "conversation_id": cid, "project_id": pid,
            "membership": "provider_reported_at_inspection_not_live_guarantee",
            "source": proof["verification_method"], "observed_at": time.time(),
            "saved_locally": False,
        })

    async def unsupported_project_operation(req):
        pid = req.match_info["project_id"]
        if not PROJECT_ID.fullmatch(pid):
            return json_error(400, "Invalid project_id")
        if store.get_project(pid) is None:
            return json_error(404, "Unknown project")
        return not_supported(pid, "Project modification or live enumeration is not verified")

    async def create_session(req):
        try:
            data = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        if not isinstance(data, dict):
            return json_error(400, "Expected JSON object")
        mode = data.get("mode", "standard")
        project_id = data.get("project_id")
        if mode not in ("standard", "temporary", "continued", "persistent"):
            return json_error(400, "mode must be temporary, continued, or persistent")
        if project_id is not None and (
            not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id)
        ):
            return json_error(400, "Invalid project_id")
        if project_id and mode not in ("continued", "persistent"):
            return json_error(400, "Project conversations require continued or persistent mode")
        url = data.get("url")
        try:
            conv, url_project = conversation_reference(url)
            body_conv, body_project = conversation_reference(data.get("conversation_id"))
            if conv and body_conv and conv != body_conv:
                return json_error(400, "Conversation URL and conversation_id differ")
            if url_project and body_project and url_project != body_project:
                return json_error(400, "Conversation references belong to different projects")
            claimed_project = url_project or body_project
            if claimed_project and claimed_project != project_id:
                return json_error(400, "Conversation project and project_id differ")
            if project_id and (url and not url_project and not body_project):
                return json_error(400, "Project session URL must include its project path")
            conv = body_conv or conv
            if project_id and conv:
                if engine.provider_status()["provider_rate_limited"]:
                    return cooldown_response()
                if not hasattr(engine.transport, "inspect_conversation"):
                    return json_error(501, "Verified project chat import unsupported")
                try:
                    verified = await engine.transport.inspect_conversation(
                        conv, project_id=project_id
                    )
                except ProjectMembershipUnsupported:
                    return not_supported(project_id, "Provider project membership evidence unavailable")
                except ProjectMembershipMismatch:
                    return json_error(409, "Provider conversation project ID disagrees")
                except Exception as exc:
                    return json_error(
                        502, "Remote chat import verification unavailable: " + type(exc).__name__
                    )
                if (
                    verified.get("project_id") != project_id
                    or verified.get("conversation_id") != conv
                ):
                    return json_error(409, "Conversation does not belong to requested project")
                if verified.get("verification_method") != "provider_conversation_project_id":
                    return not_supported(project_id, "Provider-backed project ID not corroborated")
            row = store.create_session(
                conversation_id=conv,
                title=str(data.get("title") or "")[:250],
                mode=mode,
                project_id=project_id,
            )
            if project_id and conv and verified.get("verification_method") == (
                "provider_conversation_project_id"
            ):
                store.record_session_project_verification(row["session_id"], project_id, conv)
        except (TypeError, ValueError, web.HTTPBadRequest, SessionModeError) as exc:
            return json_error(400, "Cannot create session: " + str(exc)[:160])
        return web.json_response(row, status=201)

    async def rotate_session(req):
        sid = req.match_info["session_id"]
        try:
            data = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        reason = data.get("reason", "client_requested") if isinstance(data, dict) else None
        if not isinstance(reason, str) or not (1 <= len(reason) <= 120):
            return json_error(400, "Invalid rotation reason")
        try:
            row = store.rotate_session(sid, reason=reason)
        except KeyError:
            return json_error(404, "Unknown session")
        except SessionModeError as exc:
            return json_error(409, str(exc))
        return web.json_response({**row, "previous_conversations": store.rotation_history(sid)})

    async def session_approval_observations(req):
        """Describe observed approval UI clicks, never claim provider authorization."""
        sid = req.match_info["session_id"]
        if not store.get_session(sid):
            return json_error(404, "Unknown session")
        try:
            after = int(req.query.get("after", "0"))
            limit = int(req.query.get("limit", "100"))
            if after < 0 or not 1 <= limit <= 250:
                raise ValueError("invalid pagination")
        except (TypeError, ValueError):
            return json_error(400, "Invalid pagination")
        observations = store.list_approval_observations(sid, limit=limit, after=after)
        return web.json_response({
            "session_id": sid,
            "data": observations,
            "next_cursor": observations[-1]["id"] if observations else after,
            "scope": "observed_ui_clicks_only",
            "provider_execution_verified": False,
        })

    async def session_rotation_guards(req):
        sid = req.match_info["session_id"]
        if not store.get_session(sid):
            return json_error(404, "Unknown session")
        return web.json_response({"session_id": sid, "guards": store.list_rotation_guards(sid)})

    async def put_rotation_guard(req):
        sid, gid = req.match_info["session_id"], req.match_info["guard_id"]
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,200}", gid):
            return json_error(400, "Invalid guard ID")
        try:
            data = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        if not isinstance(data, dict):
            return json_error(400, "Expected JSON object")
        owner, reason = data.get("owner"), data.get("reason")
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", owner):
            return json_error(400, "Invalid guard owner")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 200:
            return json_error(400, "Invalid guard reason")
        try:
            guard = store.put_rotation_guard(sid, gid, owner, reason)
        except KeyError:
            return json_error(404, "Unknown session")
        except SessionModeError as exc:
            return json_error(409, str(exc))
        return web.json_response(guard, status=200)

    async def delete_rotation_guard(req):
        sid, gid = req.match_info["session_id"], req.match_info["guard_id"]
        try:
            data = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        owner = data.get("owner") if isinstance(data, dict) else None
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", owner):
            return json_error(400, "Invalid guard owner")
        try:
            released = store.release_rotation_guard(sid, gid, owner)
        except KeyError:
            return json_error(404, "Unknown session")
        except SessionModeError as exc:
            return json_error(409, str(exc))
        return web.json_response({"released": released, "session_id": sid, "guard_id": gid})

    async def session_context_estimate(req):
        sid = req.match_info["session_id"]
        session = store.get_session(sid)
        if not session:
            return json_error(404, "Unknown session")
        hint = store.context_size_hint(sid)
        return web.json_response(
            {
                "session_id": sid,
                "project_id": session["project_id"],
                "conversation_id": session["conversation_id"],
                "message_count": hint["messages"],
                "local_text_chars": hint["characters"],
                "approx_text_tokens": int((hint["characters"] + 3) / 4),
                "precision": "rough_local_text_only",
                "includes_hidden_context": False,
            }
        )

    async def list_sessions(req):
        try:
            limit = min(max(int(req.query.get("limit", "50")), 1), 250)
            offset = max(int(req.query.get("offset", "0")), 0)
        except (ValueError, TypeError):
            return json_error(400, "Invalid pagination parameter")
        project_id = req.query.get("project_id")
        mode = req.query.get("mode")
        if project_id and not PROJECT_ID.fullmatch(project_id):
            return json_error(400, "Invalid project_id filter")
        if mode and mode not in ("standard", "temporary", "continued", "persistent"):
            return json_error(400, "Invalid mode filter")
        return web.json_response(
            {
                "data": store.list_sessions(
                    limit=limit, offset=offset, project_id=project_id, mode=mode
                ),
                "limit": limit,
                "offset": offset,
            }
        )

    async def get_session(req):
        row = store.get_session(req.match_info["session_id"])
        if not row:
            return json_error(404, "Unknown session")
        return web.json_response(row)

    async def send_message(req):
        sid = req.match_info["session_id"]
        if not store.get_session(sid):
            return json_error(404, "Unknown session")
        try:
            if req.content_type == "multipart/form-data":
                reader = await req.multipart()
                data = {"content": ""}
                images = []
                while part := await reader.next():
                    if part.filename:
                        raw = await part.read(decode=False)
                        images.append(
                            verify_file(raw, part.filename, part.headers.get("Content-Type", ""))
                        )
                    elif part.name in ("content", "client_request_id"):
                        data[part.name] = await part.text()
                    else:
                        raise MediaError("Unsupported multipart field")
                    if len(images) > MAX_ATTACHMENTS_PER_MESSAGE:
                        raise MediaError("Too many files")
                if images and not data.get("content"):
                    prompt, other_images = "", []
                else:
                    prompt, other_images = parse_message(data)
                images += other_images
            else:
                data = await req.json()
                prompt, images = parse_message(data)
            if sum(len(image.data) for image in images) > MAX_TOTAL_BYTES:
                raise MediaError("Total attachment payload exceeds configured limit")
        except (MediaError, ValueError, TypeError, web.HTTPException) as exc:
            return json_error(400, "Invalid message payload: " + str(exc)[:180])
        cid = data.get("client_request_id")
        if cid is not None and (not isinstance(cid, str) or not (1 <= len(cid) <= 200)):
            return json_error(400, "Invalid client_request_id")
        effort = data.get("reasoning_effort")
        if effort is not None and effort not in ("low", "medium", "high"):
            return json_error(400, "reasoning_effort must be low, medium or high")
        try:
            row, created = engine.enqueue(sid, prompt, cid, images=images, reasoning_effort=effort)
        except QueueBackpressureError:
            return web.json_response(
                {
                    "error": {"message": "Provider paused; queue full", "code": "queue_full"},
                    "rate_limit": engine.provider_status(),
                },
                status=503,
                headers={"Retry-After": str(max(1, engine.provider_status()["retry_after"]))},
            )
        except DuplicatePayloadError:
            return json_error(409, "client_request_id previously used with different content")
        except SessionModeError as exc:
            return json_error(409, str(exc))
        sess = store.get_session(sid)
        return web.json_response(
            {
                "session_id": sid,
                "conversation_id": row["conversation_id"],
                "mode": sess["mode"],
                "project_id": sess["project_id"],
                "request_id": row["request_id"],
                "status": row["status"],
                "status_url": f"/v1/requests/{row['request_id']}",
                "events_url": f"/v1/requests/{row['request_id']}/events",
                "reasoning_effort": row["reasoning_effort"] or "high",
                "images": store.list_media(row["request_id"], "user"),
                "attachments": store.list_media(row["request_id"], "user"),
                "rate_limit": engine.provider_status(),
            },
            status=202 if created else 200,
        )

    async def get_request(req):
        row = store.get_request(req.match_info["request_id"])
        if not row:
            return json_error(404, "Unknown request")
        source_session = store.get_session(row["session_id"])
        # A request's conversation is immutable even after the logical session rotates.
        row["mode"] = source_session["mode"]
        row["project_id"] = source_session["project_id"]
        # Older requests may have NULL before effective effort was persisted.
        row["reasoning_effort"] = row["reasoning_effort"] or "high"
        row["images"] = [
            m for m in store.list_media(row["request_id"]) if m["mime_type"].startswith("image/")
        ]
        row["attachments"] = store.list_media(row["request_id"])
        row["image_refs"] = store.list_image_refs(row["request_id"])
        row["artifact_refs"] = row["image_refs"]
        from .recovery import recovery_hints

        row.update(recovery_hints(row, engine.provider_status()))
        missing = [
            item["payload"]
            for item in store.get_events(row["request_id"], limit=1000)
            if item["kind"] == "file_unavailable"
        ]
        row["attachments_complete"] = not missing
        row["attachment_errors"] = missing
        return web.json_response(row)

    async def reconcile(req):
        rid = req.match_info["request_id"]
        if not store.get_request(rid):
            return json_error(404, "Unknown request")
        success = await engine.reconcile_request(rid)
        from .recovery import recovery_hints

        current = store.get_request(rid)
        return web.json_response(
            {"request_id": rid, "reconciled": success, "status": current["status"],
             **recovery_hints(current, engine.provider_status())}
        )

    async def messages(req):
        sid = req.match_info["session_id"]
        if not store.get_session(sid):
            return json_error(404, "Unknown session")
        try:
            cursor = int(req.query.get("after", "0"))
            limit = int(req.query.get("limit", "100"))
        except ValueError:
            return json_error(400, "Invalid message pagination")
        if cursor < 0 or limit < 1 or limit > 500:
            return json_error(400, "Message pagination out of bounds")
        rows = store.get_messages(sid, cursor, limit)
        for message in rows:
            message["images"] = (
                store.list_media(message["request_id"], message["role"])
                if message["request_id"]
                else []
            )
            message["attachments"] = (
                store.list_media(message["request_id"], message["role"])
                if message["request_id"]
                else []
            )
            message["image_refs"] = (
                store.list_image_refs(message["request_id"], message["role"])
                if message["request_id"]
                else []
            )
            # Provider-imported messages have no local request_id. Retain
            # metadata-only pointers separately from local binary media.
            imported_refs = store.list_message_refs(sid, message["message_id"])
            if imported_refs:
                known = {
                    ref.get("asset_pointer") or ref.get("url") or ref.get("id")
                    for ref in message["image_refs"]
                }
                message["image_refs"] += [
                    ref for ref in imported_refs
                    if (ref.get("asset_pointer") or ref.get("url") or ref.get("id"))
                    not in known
                ]
            message["artifact_refs"] = message["image_refs"]
        return web.json_response({"data": rows, "next_cursor": rows[-1]["id"] if rows else cursor})

    async def turn_state(req):
        """Read the provider-confirmed latest turn for HTTP-only watcher clients."""
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        sid = req.match_info["session_id"]
        row = store.get_session(sid)
        if not row:
            return json_error(404, "Unknown session")
        if not row["conversation_id"]:
            return json_error(400, "Session has no ChatGPT conversation")
        try:
            provider_checks["turn_state_reads"] += 1
            kwargs = {"project_id": row["project_id"]} if row.get("project_id") else {}
            history = await engine.transport.read_conversation(row["conversation_id"], **kwargs)

            chain = history.get("chain", [])
            current = chain[0] if chain else {}
            role = current.get("role") or None
            complete = (
                role == "assistant"
                and current.get("end_turn") is True
                and current.get("status") == "finished_successfully"
            )
            return web.json_response(
                {
                    "session_id": sid,
                    "conversation_id": row["conversation_id"],
                    "last_message_id": current.get("id"),
                    "last_role": role,
                    "status": current.get("status"),
                    "completed": complete,
                    "generating": not complete,
                    "content": current.get("content", "") if complete else "",
                }
            )
        except Exception as exc:
            return json_error(502, "Conversation read failed: " + str(exc)[:160])

    async def sync_session(req):
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        sid = req.match_info["session_id"]
        if not store.get_session(sid):
            return json_error(404, "Unknown session")
        try:
            seen = await engine.sync_session(sid)
            return web.json_response(
                {"session_id": sid, "seen": seen, "stored": len(store.get_messages(sid, limit=500))}
            )
        except Exception as e:
            if engine.provider_status()["provider_rate_limited"]:
                return cooldown_response()
            return json_error(502, "Conversation sync error: " + str(e)[:180])

    async def events(req):
        rid = req.match_info["request_id"]
        if not store.get_request(rid):
            return json_error(404, "Unknown request")
        try:
            cursor = max(int(req.headers.get("Last-Event-ID") or req.query.get("after", "0")), 0)
        except ValueError:
            return json_error(400, "Invalid SSE cursor")
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(req)
        try:
            while True:
                rows = store.get_events(rid, after=cursor, limit=200)
                for row in rows:
                    cursor = row["event_id"]
                    data = json.dumps(row["payload"], ensure_ascii=False, separators=(",", ":"))
                    await response.write(
                        f"id: {cursor}\nevent: {row['kind']}\ndata: {data}\n\n".encode()
                    )
                status = store.get_request(rid)["status"]
                if status in TERMINAL | {"unknown"} and not store.get_events(
                    rid, after=cursor, limit=1
                ):
                    break
                await engine.wait_events(rid, cursor, timeout=10)
                if not rows:
                    await response.write(b": heartbeat\n\n")
        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception as ex:
            # Client disconnected; task remains running independently.
            if not isinstance(ex, (ConnectionResetError, BrokenPipeError)):
                raise
        return response

    async def health(req):
        return web.json_response(
            {
                "status": "ok",
                "transport": settings.transport,
                "active": engine.active,
                "capacity": settings.max_active_sessions,
                "sessions": store.count("sessions"),
                "requests": store.count("requests"),
                "timestamp": time.time(),
                "rate_limit": engine.provider_status(),
            }
        )

    def browser_status():
        if settings.transport == "mock":
            return {"mode": "mock", "state": "ready", "cdp_connected": False}
        if browser_supervisor:
            return browser_supervisor.status()
        browser = getattr(engine.transport, "_browser", None)
        return {
            "mode": "external",
            "state": "external",
            "cdp_connected": bool(browser and browser.is_connected()),
            "cdp_url": settings.cdp_url,
        }

    async def system_status(req):
        return web.json_response(
            {
                "browser": browser_status(),
                "transport": settings.transport,
                "capacity": settings.max_active_sessions,
                "active": engine.active,
                "default_reasoning_effort": settings.reasoning_effort,
                "supported_reasoning_efforts": ["low", "medium", "high"],
                "supported_session_modes": ["standard", "temporary", "continued", "persistent"],
                "temporary_chat_supported": True,
                "rate_limit": engine.provider_status(),
            }
        )

    async def readiness(req):
        if engine.provider_status()["provider_rate_limited"]:
            return cooldown_response()
        snapshot = browser_status()
        browser_key = (snapshot.get("pid"), snapshot.get("cdp_url"), snapshot.get("state"))
        if (
            ready_cache["browser_key"] == browser_key
            and ready_cache["checked_at"]
            and time.monotonic() - ready_cache["checked_at"] < (120 if ready_cache["ready"] else 20)
        ):
            valid = ready_cache["ready"]
            return web.json_response(
                {
                    "ready": valid,
                    "browser": snapshot,
                    "chatgpt_authenticated": valid,
                    "cached": True,
                },
                status=200 if valid else 503,
            )
        if settings.transport == "mock":
            return web.json_response(
                {"ready": True, "browser": snapshot, "chatgpt_authenticated": None}
            )
        if browser_supervisor and not browser_supervisor.cdp_url:
            return web.json_response(
                {
                    "ready": False,
                    "browser": snapshot,
                    "chatgpt_authenticated": False,
                    "reason": "browser_unavailable",
                },
                status=503,
            )
        try:
            browser = await engine.transport._ensure()
            page = await engine.transport._new_page(browser)
            try:
                page.on("response", engine.transport._observe_provider_response)
                provider_checks["readiness_page_loads"] += 1
                await page.goto(
                    "https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000
                )
                editor = page.locator("div[contenteditable=true].ProseMirror").first
                await editor.wait_for(state="visible", timeout=12000)
            finally:
                await page.close()
            ready_cache.update(checked_at=time.monotonic(), browser_key=browser_key, ready=True)
            return web.json_response(
                {"ready": True, "browser": snapshot, "chatgpt_authenticated": True}
            )
        except Exception:
            if engine.provider_status()["provider_rate_limited"]:
                return cooldown_response()
            ready_cache.update(checked_at=time.monotonic(), browser_key=browser_key, ready=False)
            return web.json_response(
                {
                    "ready": False,
                    "browser": snapshot,
                    "chatgpt_authenticated": False,
                    "reason": "auth_required_or_browser_unavailable",
                },
                status=503,
            )

    async def metrics(req):
        return web.json_response(
            {
                "active_slots": engine.active,
                "max_active_slots": engine.max_seen,
                "capacity": settings.max_active_sessions,
                "queued": sum(
                    1 for r in store.db.execute("SELECT status FROM requests WHERE status='queued'")
                ),
                "sessions": store.count("sessions"),
                "events": store.count("events"),
                "rate_limit": engine.provider_status(),
                "provider_checks": {
                    **provider_checks,
                    "history_reads": getattr(engine.transport, "_history_reads", 0),
                    "provider_429_events": getattr(engine.transport, "_throttle_events", 0),
                },
            }
        )

    async def openapi_schema(req):
        """Serve generated local API schema without provider access."""
        schema_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
        if not schema_path.exists():
            return json_error(503, "API schema not installed")
        return web.json_response(json.loads(schema_path.read_text(encoding="utf-8")))

    async def read_media(req):
        row = store.get_media(req.match_info["media_id"])
        if not row:
            return json_error(404, "Unknown media")
        return web.Response(
            body=row["content"],
            content_type=row["mime_type"],
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": (
                    "inline"
                    if row["mime_type"].startswith("image/")
                    else "attachment; filename*=UTF-8''"
                    + __import__("urllib.parse", fromlist=["quote"]).quote(row["filename"])
                ),
            },
        )

    async def models(req):
        return web.json_response({"object": "list", "data": [{"id": "auto", "object": "model"}]})

    async def chat_completions(req):
        """Standard client shape; native API is preferred for async supervision.

        Chat Completions stream returns the fully reconciled result as one delta,
        because provisional DOM text may later require a text_replace event.
        """
        try:
            body = await req.json()
        except (ValueError, TypeError, web.HTTPException):
            return json_error(400, "Invalid JSON object")
        if not isinstance(body, dict):
            return json_error(400, "Expected JSON object")
        turns = body.get("messages", [])
        if not isinstance(turns, list):
            return json_error(400, "messages must be a list")
        users = [x for x in turns if isinstance(x, dict) and x.get("role") == "user"]
        if not users:
            return json_error(400, "At least one user message is required")
        try:
            content, images = parse_message({"content": users[-1].get("content", "")})
            if sum(len(i.data) for i in images) > MAX_TOTAL_BYTES:
                raise MediaError("Total attachment payload exceeds configured limit")
        except MediaError as exc:
            return json_error(400, str(exc))
        sid = body.get("session_id")
        if sid:
            existing = store.get_session(sid)
            if not existing:
                return json_error(404, "Unknown session_id")
            for key in ("mode", "project_id"):
                if key in body and body[key] != existing[key]:
                    return json_error(
                        409, "Cannot change session " + key + " when reusing session_id"
                    )
        else:
            mode = body.get("mode", "standard")
            project_id = body.get("project_id")
            if mode not in ("standard", "temporary", "continued", "persistent"):
                return json_error(400, "Invalid session mode")
            if project_id is not None and (
                not isinstance(project_id, str)
                or not PROJECT_ID.fullmatch(project_id)
                or mode not in ("continued", "persistent")
            ):
                return json_error(400, "Project requires continued/persistent mode and valid ID")
            try:
                cid, url_project = conversation_reference(body.get("conversation_id"))
                if url_project and url_project != project_id:
                    return json_error(400, "Conversation URL and project_id differ")
                if project_id and cid:
                    if engine.provider_status()["provider_rate_limited"]:
                        return cooldown_response()
                    if not hasattr(engine.transport, "inspect_conversation"):
                        return json_error(501, "Verified project chat import unsupported")
                    try:
                        verified = await engine.transport.inspect_conversation(
                            cid, project_id=project_id
                        )
                    except ProjectMembershipUnsupported:
                        return not_supported(project_id, "Provider project membership evidence unavailable")
                    except ProjectMembershipMismatch:
                        return json_error(409, "Provider conversation project ID disagrees")
                    except Exception as exc:
                        return json_error(
                            502,
                            "Remote chat import verification unavailable: " + type(exc).__name__,
                        )
                    if (
                        verified.get("project_id") != project_id
                        or verified.get("conversation_id") != cid
                    ):
                        return json_error(409, "Conversation does not belong to requested project")
                    if verified.get("verification_method") != "provider_conversation_project_id":
                        return not_supported(project_id, "Provider-backed project ID not corroborated")
                sid = store.create_session(conversation_id=cid, mode=mode, project_id=project_id)[
                    "session_id"
                ]
                if project_id and cid and verified.get("verification_method") == (
                    "provider_conversation_project_id"
                ):
                    store.record_session_project_verification(sid, project_id, cid)
            except (TypeError, ValueError, web.HTTPBadRequest) as exc:
                return json_error(400, "Invalid session settings: " + str(exc)[:140])
        key = body.get("client_request_id")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 200):
            return json_error(400, "Invalid client_request_id")
        effort = body.get("reasoning_effort")
        if effort is not None and effort not in ("low", "medium", "high"):
            return json_error(400, "reasoning_effort must be low, medium or high")
        try:
            result, _ = engine.enqueue(sid, content, key, images=images, reasoning_effort=effort)
        except QueueBackpressureError:
            return cooldown_response()
        except DuplicatePayloadError:
            return json_error(409, "client_request_id conflict")
        except SessionModeError as exc:
            return json_error(409, str(exc))
        rid = result["request_id"]
        created = int(time.time())

        async def await_result():
            await engine.wait_terminal(rid, timeout=settings.hard_timeout_seconds + 120)
            return store.get_request(rid)

        def envelope(row):
            return {
                "id": "chatcmpl-" + rid,
                "object": "chat.completion",
                "created": created,
                "model": "auto",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": row["answer"]},
                    }
                ],
                "session_id": sid,
                "conversation_id": row["conversation_id"],
                "request_id": rid,
                "images": store.list_media(rid, "assistant"),
                "image_refs": store.list_image_refs(rid, "assistant"),
                "attachments": store.list_media(rid, "assistant"),
                "artifact_refs": store.list_image_refs(rid, "assistant"),
            }

        if not body.get("stream", False):
            try:
                row = await await_result()
            except asyncio.TimeoutError:
                return json_error(504, "Chat completion timeout")
            if row["status"] != "completed":
                return json_error(502, "Chat not complete: " + row["status"])
            return web.json_response(envelope(row))
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        await response.prepare(req)
        try:
            row = await await_result()
            if row["status"] == "completed":
                message = {
                    "id": "chatcmpl-" + rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": "auto",
                    "images": store.list_media(rid, "assistant"),
                    "image_refs": store.list_image_refs(rid, "assistant"),
                    "attachments": store.list_media(rid, "assistant"),
                    "artifact_refs": store.list_image_refs(rid, "assistant"),
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": row["answer"]},
                            "finish_reason": None,
                        }
                    ],
                }
                await response.write(
                    ("data: " + json.dumps(message, ensure_ascii=False) + "\n\n").encode()
                )
                end = {**message, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                await response.write(("data: " + json.dumps(end) + "\n\n").encode())
            else:
                await response.write(
                    (
                        "data: "
                        + json.dumps({"error": row["last_error"] or row["status"]})
                        + "\n\n"
                    ).encode()
                )
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionError, asyncio.CancelledError):
            pass
        return response

    app.router.add_post("/v1/chats/resolve-marker", resolve_chat_marker)
    app.router.add_post("/v1/cleanup/plan", cleanup_plan)
    app.router.add_get("/v1/cleanup/operations/{operation_id}", deletion_operation)
    app.router.add_post("/v1/cleanup/operations/{operation_id}/reconcile", reconcile_deletion)
    app.router.add_get("/v1/sessions/{session_id}/deletion-preview", deletion_preview)
    app.router.add_post("/v1/sessions/{session_id}/deletion", delete_remote_conversation)
    app.router.add_delete("/v1/sessions/{session_id}", delete_local_session)
    app.router.add_post("/v1/projects", create_project)
    app.router.add_post("/v1/projects/verify", verify_existing_project)
    app.router.add_get("/v1/projects/operations/{operation_id}", get_project_creation)
    app.router.add_get("/v1/projects", list_projects)
    app.router.add_get("/v1/projects/{project_id}", get_project)
    app.router.add_get("/v1/projects/{project_id}/metadata", project_metadata)
    app.router.add_get("/v1/projects/{project_id}/live-metadata", live_project_metadata)
    app.router.add_get(
        "/v1/projects/{project_id}/chats/{conversation_id}/verify", verify_chat_membership
    )
    app.router.add_get("/v1/projects/{project_id}/chats", unsupported_project_operation)
    app.router.add_get("/v1/projects/{project_id}/sources", unsupported_project_operation)
    app.router.add_get("/v1/projects/{project_id}/files", unsupported_project_operation)
    app.router.add_post("/v1/projects/{project_id}/files", unsupported_project_operation)
    app.router.add_get("/v1/projects/{project_id}/instructions", unsupported_project_operation)
    app.router.add_put("/v1/projects/{project_id}/instructions", unsupported_project_operation)
    app.router.add_patch("/v1/projects/{project_id}", unsupported_project_operation)
    app.router.add_put("/v1/projects/{project_id}/memory", unsupported_project_operation)
    app.router.add_post("/v1/sessions", create_session)
    app.router.add_post("/v1/sessions/{session_id}/rotate", rotate_session)
    app.router.add_get("/v1/sessions/{session_id}/approval-observations", session_approval_observations)
    app.router.add_get("/v1/sessions/{session_id}/rotation-guards", session_rotation_guards)
    app.router.add_put("/v1/sessions/{session_id}/rotation-guards/{guard_id}", put_rotation_guard)
    app.router.add_delete("/v1/sessions/{session_id}/rotation-guards/{guard_id}", delete_rotation_guard)
    app.router.add_get("/v1/sessions/{session_id}/context-estimate", session_context_estimate)
    app.router.add_get("/v1/sessions", list_sessions)
    app.router.add_get("/v1/sessions/{session_id}", get_session)
    app.router.add_get("/v1/sessions/{session_id}/messages", messages)
    app.router.add_post("/v1/sessions/{session_id}/sync", sync_session)
    app.router.add_get("/v1/sessions/{session_id}/turn-state", turn_state)
    app.router.add_post("/v1/sessions/{session_id}/messages", send_message)
    app.router.add_get("/v1/requests/{request_id}", get_request)
    app.router.add_post("/v1/requests/{request_id}/reconcile", reconcile)
    app.router.add_get("/v1/requests/{request_id}/events", events)
    app.router.add_get("/health", health)
    app.router.add_get("/ready", readiness)
    app.router.add_get("/v1/system/status", system_status)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/v1/openapi.json", openapi_schema)
    app.router.add_get("/v1/media/{media_id}", read_media)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app
