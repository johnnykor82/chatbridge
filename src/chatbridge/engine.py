"""Request scheduler: durable queue, one in-flight turn per session, bounded concurrency."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid
from contextlib import aclosing

from .browser_transport import SubmissionUncertainError
from .media import ImagePayload
from .model import ChatTransport
from .rate_limit import ProviderRateLimitError
from .store import TERMINAL, Store

logger = logging.getLogger(__name__)


def _watcher_receipt_only(request: dict) -> bool:
    """Only source-bound Watcher delivery notices need a user-message receipt.

    All ordinary requests still require an actual assistant end_turn. Neither
    a matching prompt alone nor an arbitrary client key enables early finish.
    """
    key = request.get("client_request_id") or ""
    prompt = request.get("prompt") or ""
    individual = re.fullmatch(r"watcher-delivery-([0-9a-f]{32})", key)
    if individual:
        return prompt.startswith(f"[MC-WATCH:{individual.group(1)}]")
    batch = re.fullmatch(r"watcher-batch-([0-9a-f]{32})", key)
    if batch and prompt.startswith(
        "Watcher: Multiple notifications were queued while the orchestrator was busy."
    ):
        tokens = re.findall(r"\\[MC-WATCH:([0-9a-f]{32})\\]", prompt)
        return len(set(tokens)) >= 2
    return False


class QueueBackpressureError(RuntimeError):
    """Persistent pending queue is full while the provider is cooling down."""


class GatewayEngine:
    def __init__(
        self,
        store: Store,
        transport: ChatTransport,
        max_active: int = 10,
        default_reasoning_effort: str = "high",
        max_queued: int = 200,
    ):
        if default_reasoning_effort not in ("low", "medium", "high"):
            raise ValueError("Invalid default reasoning effort")
        self.default_reasoning_effort = default_reasoning_effort
        self.store = store
        self.transport = transport
        self.max_active = max_active
        self.max_queued = max_queued
        self._sem = asyncio.Semaphore(max_active)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._condition = asyncio.Condition()
        self.active = 0
        self.max_seen = 0
        self._stopping = False
        self._reconcile_task: asyncio.Task | None = None
        self._recovery_monitor_task: asyncio.Task | None = None
        self._recovery_last_check: dict[str, float] = {}
        self._rate_limit_gate = asyncio.Lock()
        self._next_probe_at = 0.0
        if hasattr(self.transport, "set_rate_limit_handler"):
            self.transport.set_rate_limit_handler(self.note_provider_rate_limit)
        if hasattr(self.transport, "set_rate_limit_checker"):
            self.transport.set_rate_limit_checker(
                lambda: self.store.rate_limit_status()["retry_after"]
            )
        if hasattr(self.transport, "set_context_estimator"):
            self.transport.set_context_estimator(self.store.context_size_hint)

    def note_provider_rate_limit(self, seconds: float, source: str) -> None:
        state = self.store.note_provider_rate_limit(seconds, reason=source)
        logger.warning(
            "Provider rate limit; all new submissions paused for %ss",
            state["retry_after"],
        )

    async def _await_provider_slot(self, *, pace: bool = True) -> None:
        """Serialize the recovery probe; no provider traffic during cooldown."""
        async with self._rate_limit_gate:
            while not self._stopping:
                state = self.store.rate_limit_status()
                if state["provider_rate_limited"]:
                    await asyncio.sleep(min(5.0, max(0.05, state["retry_after"])))
                    continue
                elapsed = time.monotonic()
                # First minute after cooldown: paced, rather than releasing
                # all queued work at the same time.
                if pace and self._next_probe_at > elapsed:
                    await asyncio.sleep(self._next_probe_at - elapsed)
                    continue
                until = state["paused_until"]
                if pace:
                    self._next_probe_at = (
                        time.monotonic() + 5.0
                        if until is not None and time.time() < until + 60.0
                        else 0.0
                    )
                return
            raise asyncio.CancelledError

    def provider_status(self) -> dict:
        return self.store.rate_limit_status()

    async def start(self) -> None:
        count = self.store.reconcile_after_restart()
        if count:
            logger.warning("Reconciliation required for %d interrupted requests", count)
        for row in self.store.pending():
            self._schedule(row["request_id"], row["session_id"])
        if self.store.unresolved():
            self._reconcile_task = asyncio.create_task(self.reconcile_unknown())
        # The daemon may survive a network outage without restarting. Poll
        # only uncertain durable records; provider reads are rate-limited.
        self._recovery_monitor_task = asyncio.create_task(
            self._recovery_monitor(), name="chatbridge:outage-recovery"
        )

    def enqueue(
        self,
        session_id: str,
        prompt: str,
        client_request_id: str | None = None,
        images: list[ImagePayload] | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict, bool]:
        rid = client_request_id or str(uuid.uuid4())
        effective_effort = reasoning_effort or self.default_reasoning_effort
        if effective_effort not in ("low", "medium", "high"):
            raise ValueError("Unsupported reasoning effort")
        # Backpressure applies to new work only: idempotent repeats must
        # still resolve to their original request even when the queue is full.
        if self.provider_status()["provider_rate_limited"]:
            queued = self.store.db.execute(
                "SELECT COUNT(*) FROM requests WHERE status='queued'"
            ).fetchone()[0]
            known = self.store.db.execute(
                "SELECT 1 FROM requests WHERE session_id=? AND client_request_id=?",
                (session_id, rid),
            ).fetchone()
            if queued >= self.max_queued and not known:
                raise QueueBackpressureError("Provider paused; durable queue capacity reached")
        result, created = self.store.enqueue(
            session_id=session_id,
            prompt=prompt,
            client_request_id=rid,
            images=images,
            reasoning_effort=effective_effort,
        )
        if created:
            self._schedule(result["request_id"], session_id)
        return result, created

    def _schedule(self, rid: str, sid: str):
        if rid not in self._tasks:
            task = asyncio.create_task(self._run(rid, sid), name="chatbridge:" + rid)
            self._tasks[rid] = task
            task.add_done_callback(lambda _: self._tasks.pop(rid, None))

    async def _emit(self, rid: str, sid: str, kind: str, **data):
        session = self.store.get_session(sid)
        payload = {
            "request_id": rid,
            "session_id": sid,
            "conversation_id": session["conversation_id"] if session else None,
            "type": kind,
            **data,
        }
        event_id = self.store.append_event(sid, rid, kind, payload)
        async with self._condition:
            self._condition.notify_all()
        return event_id

    async def _run(self, rid: str, sid: str):
        while not self._stopping:
            await self._run_once(rid, sid)
            row = self.store.get_request(rid)
            if not row or row["status"] != "queued":
                return

    async def _run_once(self, rid: str, sid: str):
        lock = self._session_locks.setdefault(sid, asyncio.Lock())
        # Per-session lock BEFORE global slot prevents same-conversation work occupying slots.
        async with lock:
            await self._await_provider_slot()
            async with self._sem:
                await self._await_provider_slot(pace=False)
                self.active += 1
                self.max_seen = max(self.max_seen, self.active)
                confirmed_send = False
                try:
                    request = self.store.get_request(rid)
                    if not request or request["status"] != "queued":
                        return
                    session = self.store.get_session(sid)
                    if not session:
                        raise RuntimeError("Session disappeared")
                    if session.get("conversation_id"):
                        # Other requests may have been queued before the first
                        # message established this physical conversation.
                        self.store.record_request_conversation(rid, session["conversation_id"])
                    self.store.set_status(rid, "submitting")
                    await self._emit(rid, sid, "status", status="submitting")
                    prompt = request["prompt"]
                    images = self.store.image_payloads(rid)
                    opts = dict(
                        session_id=sid,
                        conversation_id=session["conversation_id"],
                        prompt=prompt,
                        request_id=rid,
                    )
                    if images:
                        opts["images"] = images
                    if session.get("project_id"):
                        opts["project_id"] = session["project_id"]
                    if session.get("mode") == "temporary":
                        opts["temporary"] = True
                    # Transport already carries the configured default. Only
                    # per-turn overrides need an extra keyword, preserving
                    # compatibility with older test/custom transports.
                    if (
                        request.get("reasoning_effort")
                        and request["reasoning_effort"] != self.default_reasoning_effort
                    ):
                        opts["reasoning_effort"] = request["reasoning_effort"]
                    # Close transport before releasing the global concurrency slot.
                    async with aclosing(self.transport.generate(**opts)) as events:
                        async for ev in events:
                            if (
                                session.get("mode") != "temporary"
                                and ev.conversation_id
                                and ev.conversation_id != session["conversation_id"]
                            ):
                                self.store.bind_conversation(
                                    sid, ev.conversation_id, request_id=rid
                                )
                                session = self.store.get_session(sid)
                            if ev.type == "conversation_bound":
                                await self._emit(
                                    rid,
                                    sid,
                                    "conversation_bound",
                                    conversation_id=ev.conversation_id,
                                )
                            elif ev.type == "submitted":
                                confirmed_send = True
                                if ev.message_id:
                                    self.store.set_ids(
                                        rid, user_message_id=ev.message_id,
                                        parent_message_id=ev.parent_message_id,
                                    )
                                self.store.insert_message(
                                    sid, "user", prompt, request_id=rid, message_id=ev.message_id
                                )
                                # Watcher notifications are messages TO the
                                # orchestrator, not a request for a model reply.
                                # The provider-assigned user-message ID, exact
                                # immutable request key and physical chat binding
                                # prove receipt. Never fabricate an assistant turn.
                                if (
                                    ev.message_id and ev.conversation_id
                                    and ev.conversation_id == session.get("conversation_id")
                                    and _watcher_receipt_only(request)
                                ):
                                    self.store.set_status(rid, "completed")
                                    await self._emit(
                                        rid, sid, "completed", answer="",
                                        receipt_only=True,
                                        user_message_id=ev.message_id,
                                    )
                                    return  # aclosing frees browser and provider slot
                                await self._emit(rid, sid, "status", status="thinking")
                                self.store.set_status(rid, "thinking")
                            elif ev.type == "text_delta":
                                self.store.append_answer(rid, ev.content)
                                self.store.set_status(rid, "streaming")
                                await self._emit(rid, sid, "text_delta", text=ev.content)
                            elif ev.type == "text_replace":
                                self.store.replace_answer(rid, ev.content)
                                self.store.set_status(rid, "streaming")
                                await self._emit(rid, sid, "text_replace", text=ev.content)
                            elif ev.type in ("thinking", "tool_running", "awaiting_approval"):
                                self.store.set_status(rid, ev.type)
                                await self._emit(
                                    rid,
                                    sid,
                                    "status",
                                    status=ev.type,
                                    tool=ev.tool,
                                    operation=ev.operation,
                                )
                            elif ev.type == "approval_applied":
                                self.store.add_approval(
                                    rid,
                                    sid,
                                    session["conversation_id"],
                                    ev.tool,
                                    ev.operation,
                                    ev.scope or "once",
                                    (ev.meta or {}).get("result", "applied"),
                                )
                                await self._emit(
                                    rid,
                                    sid,
                                    "approval_applied",
                                    scope=ev.scope,
                                    tool=ev.tool,
                                    operation=ev.operation,
                                    result=(ev.meta or {}).get("result", "unverified"),
                                    provider_execution_verified=False,
                                )
                            elif ev.type == "tool_started" or ev.type == "tool_finished":
                                await self._emit(
                                    rid, sid, ev.type, tool=ev.tool, operation=ev.operation
                                )
                            elif ev.type in ("image_refs", "artifact_refs"):
                                refs = (ev.meta or {}).get("refs", [])
                                self.store.save_image_refs(rid, sid, "assistant", refs)
                                await self._emit(rid, sid, ev.type, refs=refs)
                            elif ev.type == "file_unavailable":
                                await self._emit(rid, sid, "file_unavailable", **(ev.meta or {}))
                            elif ev.type in ("image", "artifact"):
                                meta = ev.meta or {}
                                raw = base64.b64decode(meta["data_base64"], validate=True)
                                from .media import verify_file

                                image = verify_file(
                                    raw,
                                    meta.get("filename", "generated-image"),
                                    meta.get("mime_type", ""),
                                )
                                image_id = self.store.save_image(rid, sid, image, role="assistant")
                                await self._emit(
                                    rid,
                                    sid,
                                    "image" if image.mime_type.startswith("image/") else "artifact",
                                    image_id=image_id,
                                    url=f"/v1/media/{image_id}",
                                    mime_type=image.mime_type,
                                    filename=image.name,
                                    size=len(image.data),
                                )
                            elif ev.type == "completed":
                                if ev.message_id:
                                    self.store.set_ids(rid, assistant_message_id=ev.message_id)
                                self.store.set_status(rid, "completed")
                                answer = self.store.get_request(rid)["answer"]
                                self.store.insert_message(
                                    sid,
                                    "assistant",
                                    answer,
                                    request_id=rid,
                                    message_id=ev.message_id,
                                    parent_message_id=ev.parent_message_id,
                                )
                                await self._emit(
                                    rid,
                                    sid,
                                    "completed",
                                    answer=answer,
                                    images=self.store.list_media(rid, "assistant"),
                                    image_refs=self.store.list_image_refs(rid, "assistant"),
                                )
                                return
                            elif ev.type == "error":
                                raise RuntimeError(ev.content or "transport error")
                        raise RuntimeError("Transport returned without final turn confirmation")
                except ProviderRateLimitError:
                    if not self.provider_status()["provider_rate_limited"]:
                        self.note_provider_rate_limit(60.0, "provider_rate_limit_error")
                    if confirmed_send:
                        # Fail closed: after submission, any uncertain error
                        # must never schedule a duplicate user message.
                        self.store.set_status(rid, "unknown", error="provider rate limited")
                        await self._emit(rid, sid, "error", status="unknown")
                    else:
                        self.store.set_status(rid, "queued")
                        await self._emit(rid, sid, "status", status="queued", rate_limited=True)
                except asyncio.CancelledError:
                    # The UI may have sent before emitting its first submitted
                    # event. Cancellation inside this phase is always ambiguous.
                    # Only cancellation BEFORE submitting is safely queued.
                    self.store.set_status(rid, "unknown", error="service interrupted")
                    raise
                except Exception as exc:
                    # Once submitted, never automatically retransmit after an uncertain failure.
                    status = (
                        "unknown"
                        if (confirmed_send or isinstance(exc, SubmissionUncertainError))
                        else "failed"
                    )
                    self.store.set_status(
                        rid, status, error=f"{type(exc).__name__}: {str(exc)[:250]}"
                    )
                    await self._emit(
                        rid,
                        sid,
                        "error",
                        status=status,
                        error=f"{type(exc).__name__}: {str(exc)[:250]}",
                    )
                    logger.exception("Request %s failed (%s)", rid, status)
                finally:
                    self.active -= 1

    async def wait_terminal(self, rid: str, timeout: float = 10):
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(
                    lambda: (
                        (self.store.get_request(rid) or {}).get("status") in TERMINAL | {"unknown"}
                    )
                ),
                timeout,
            )

    async def wait_events(self, rid: str, cursor: int, timeout: float = 15):
        async with self._condition:
            if self.store.get_events(rid, after=cursor):
                return
            try:
                await asyncio.wait_for(self._condition.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    async def sync_session(self, sid: str) -> int:
        if self.provider_status()["provider_rate_limited"]:
            raise ProviderRateLimitError("Provider cooldown active")
        session = self.store.get_session(sid)
        if not session:
            raise KeyError(sid)
        cid = session["conversation_id"]
        if not cid:
            return 0
        kwargs = {"project_id": session["project_id"]} if session.get("project_id") else {}
        history = await self.transport.read_conversation(cid, **kwargs)
        if history.get("conversation_id", cid) != cid:
            raise ValueError("Provider conversation ID differs from requested chat")
        if history.get("chain_complete") is False:
            raise ValueError("Provider selected branch traversal was incomplete")
        chain = history.get("chain", [])
        # The selected branch must be a contiguous provider parent chain.
        if len({msg.get("id") for msg in chain}) != len(chain) or any(
            not msg.get("id") for msg in chain
        ):
            raise ValueError("Provider history contains missing or duplicate message IDs")
        for newer, older in zip(chain, chain[1:]):
            parent = newer.get("branch_parent_id", newer.get("parent_id"))
            if ("branch_parent_id" in newer or "parent_id" in newer) and parent != older.get("id"):
                raise ValueError("Provider history contains a broken parent chain")
        count = 0
        for msg in reversed(chain):
            role = msg.get("role")
            if role not in ("user", "assistant", "tool"):
                continue
            mid = msg.get("id")
            self.store.insert_message(
                sid, role, msg.get("content", ""), message_id=mid,
                parent_message_id=msg.get("parent_id"),
                created_at=msg.get("create_time"),
            )
            self.store.save_message_refs(
                sid, mid, role, msg.get("images", []) + msg.get("attachments", [])
            )
            count += 1
        return count

    async def reconcile_request(self, rid: str) -> bool:
        """Provider GET-only unknown recovery. Never retry an uncertain Send."""
        if self.provider_status()["provider_rate_limited"]:
            return False
        row = self.store.get_request(rid)
        if not row or row["status"] != "unknown":
            return False
        session = self.store.get_session(row["session_id"])
        if not session:
            return False
        # The queued request's persisted PHYSICAL conversation is binding.
        # Never substitute the session's possibly rotated current chat.
        conversation_id = row.get("conversation_id")
        if not conversation_id:
            return False
        try:
            kwargs = {"project_id": session["project_id"]} if session.get("project_id") else {}
            history = await self.transport.read_conversation(conversation_id, **kwargs)
            if history.get("conversation_id", conversation_id) != conversation_id:
                return False
            if history.get("chain_complete") is False:
                return False
            from .browser_transport import _match_turn, _turn_image_refs
            from .receipt_recovery import exact_user_receipt

            chain = history.get("chain", [])
            bound_new = False
            if not row.get("user_message_id"):
                # The provider's selected branch must be provably complete,
                # especially before recovering a missing Send receipt.
                if history.get("chain_complete") is not True:
                    return False
                # Text alone cannot confirm an attachment-bearing request.
                if self.store.list_media(rid) or self.store.list_image_refs(rid):
                    return False
                receipt = exact_user_receipt(
                    chain, prompt=row["prompt"],
                    request_created_at=row["created_at"],
                    expected_parent_id=row.get("parent_message_id"),
                )
                if receipt is None:
                    return False
                watcher_receipt = _watcher_receipt_only(row)
                bound_new = self.store.bind_verified_unknown_user_receipt(
                    rid, row["session_id"], conversation_id,
                    receipt["id"], receipt.get("parent_id"),
                    row["prompt"], float(receipt["create_time"]),
                    receipt_only=watcher_receipt,
                    provider_content=receipt["content"],
                )
                if not bound_new:
                    return False
                if watcher_receipt:
                    # A Watcher notice is a user message delivered TO the
                    # orchestrator. It does not require assistant completion.
                    async with self._condition:
                        self._condition.notify_all()
                    return True
                row = self.store.get_request(rid)

            final = _match_turn(
                chain, row["user_message_id"], row["prompt"],
                expected_parent_id=row.get("parent_message_id"),
            )
            if not final or not final.get("id"):
                # The user message is proven, but model end_turn is not.
                # Keep the ordinary request unknown; never fabricate success.
                return bound_new
            text = final.get("content", "")
            refs = _turn_image_refs(
                chain, row["user_message_id"], row["prompt"],
                expected_parent_id=row.get("parent_message_id"),
            )
            saved = self.store.complete_recovered_request(
                rid, row["session_id"], conversation_id, final["id"],
                final.get("parent_id"), text, refs,
            )
            if saved:
                async with self._condition:
                    self._condition.notify_all()
            return saved or bound_new
        except ProviderRateLimitError:
            if not self.provider_status()["provider_rate_limited"]:
                self.note_provider_rate_limit(60, "reconciliation_rate_limited")
            return False
        except Exception as exc:
            logger.warning("Reconciliation unavailable for %s: %s",
                           rid, type(exc).__name__)
            return False

    async def reconcile_unknown(self) -> None:
        """Paced provider-history GET of only actionable unknown receipts."""
        if self.provider_status()["provider_rate_limited"]:
            return
        # Unconfirmed ordinary turns can also lose the provider user ID.
        # They remain UNKNOWN until a separately verified assistant end_turn;
        # only Watcher notifications may finish on a user receipt alone.
        candidates = [
            row for row in self.store.unresolved()
            if row.get("conversation_id")
            and self.store.recovery_probe_due(row["request_id"])
        ]
        candidates.sort(key=lambda row: (
            self._recovery_last_check.get(row["request_id"], 0),
            0 if _watcher_receipt_only(row) else 1,
            row["created_at"],
        ))
        for index, row in enumerate(candidates[:2]):
            if self._stopping or self.provider_status()["provider_rate_limited"]:
                return
            if index:
                await asyncio.sleep(12)
            if self._stopping or self.provider_status()["provider_rate_limited"]:
                return
            # Reserve BEFORE touching the provider. An interrupted GET or
            # daemon restart cannot immediately repeat the same probe.
            if not self.store.reserve_recovery_probe(row["request_id"]):
                continue
            self._recovery_last_check[row["request_id"]] = time.monotonic()
            await self.reconcile_request(row["request_id"])

    async def _recovery_monitor(self) -> None:
        """Bounded wakeups after connectivity restoration; never POST Send."""
        try:
            # Initial startup reconciliation is separately scheduled. Delay
            # the next cycle to avoid duplicate provider-history page reads.
            while not self._stopping:
                await asyncio.sleep(120)
                if self._stopping or self.provider_status()["provider_rate_limited"]:
                    continue
                if (self.store.unresolved()
                        and (self._reconcile_task is None
                             or self._reconcile_task.done())):
                    self._reconcile_task = asyncio.create_task(
                        self.reconcile_unknown()
                    )
                    await self._reconcile_task
        except asyncio.CancelledError:
            raise

    async def stop(self):
        self._stopping = True
        if self._recovery_monitor_task and not self._recovery_monitor_task.done():
            self._recovery_monitor_task.cancel()
            await asyncio.gather(self._recovery_monitor_task,
                                 return_exceptions=True)
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
        for task in tuple(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)
        await self.transport.close()
