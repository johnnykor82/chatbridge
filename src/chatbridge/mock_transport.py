"""Deterministic mock used to test HTTP and scheduling without the user's account."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from .model import TransportEvent


class MockTransport:
    def __init__(self, delay: float = 0.03):
        self.delay = delay
        self.active = 0
        self.max_seen = 0
        self.calls: list[tuple[str, str]] = []
        self._projects: dict[str, dict] = {}
        self.deleted_conversations: set[str] = set()
        self.deletion_calls: list[str] = []
        self.deletion_failure: str | None = None

    async def generate(
        self,
        *,
        session_id: str,
        conversation_id: str | None,
        prompt: str,
        request_id: str,
        images=None,
        reasoning_effort=None,
        project_id=None,
        temporary=False,
    ) -> AsyncIterator[TransportEvent]:
        self.calls.append((session_id, prompt))
        self.active += 1
        self.max_seen = max(self.max_seen, self.active)
        try:
            conv = conversation_id or str(uuid.uuid4())
            yield TransportEvent("submitted", conversation_id=conv, message_id=str(uuid.uuid4()))
            yield TransportEvent("thinking")
            await asyncio.sleep(self.delay)
            yield TransportEvent("text_delta", content="Echo: " + prompt)
            yield TransportEvent("completed", conversation_id=conv, message_id=str(uuid.uuid4()))
        finally:
            self.active -= 1

    async def create_project(self, name: str, memory_mode: str) -> str:
        project_id = "g-p-" + uuid.uuid4().hex
        self._projects[project_id] = {
            "project_id": project_id,
            "name": name,
            "memory_mode": memory_mode,
        }
        return project_id

    async def inspect_conversation(self, conversation_id: str, *, project_id: str) -> dict:
        return {"conversation_id": conversation_id, "project_id": project_id,
                "verification_method": "provider_conversation_project_id"}

    async def inspect_project(self, project_id: str) -> dict:
        if project_id not in self._projects:
            raise ValueError("Unknown remote project")
        return dict(self._projects[project_id])

    async def delete_conversation(self, conversation_id: str) -> bool:
        self.deletion_calls.append(conversation_id)
        if self.deletion_failure:
            raise RuntimeError(self.deletion_failure)
        self.deleted_conversations.add(conversation_id)
        return True

    async def verify_conversation_deleted(self, conversation_id: str) -> bool:
        return conversation_id in self.deleted_conversations

    async def read_conversation(self, conversation_id: str, *, project_id=None) -> dict:
        return {"conversation_id": conversation_id, "messages": []}

    async def close(self):
        pass
