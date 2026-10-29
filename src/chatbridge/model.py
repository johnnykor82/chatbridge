"""Gateway domain objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from .media import ImagePayload


@dataclass(frozen=True)
class TransportEvent:
    type: str
    content: str = ""
    conversation_id: str | None = None
    message_id: str | None = None
    parent_message_id: str | None = None
    tool: str | None = None
    operation: str | None = None
    scope: str | None = None
    meta: dict | None = None


class ChatTransport(Protocol):
    async def generate(
        self,
        *,
        session_id: str,
        conversation_id: str | None,
        prompt: str,
        request_id: str,
        images: list[ImagePayload] | None = None,
        reasoning_effort: str | None = None,
        project_id: str | None = None,
        temporary: bool = False,
    ) -> AsyncIterator[TransportEvent]: ...
    async def read_conversation(self, conversation_id: str) -> dict: ...
    async def close(self) -> None: ...
