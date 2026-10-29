"""Fail-closed proof rules for project/chat metadata fetched read-only.

A project-shaped URL and visible messages are *not* proof of membership.
Only a provider-reported, exact conversation project ID can establish it.
"""

from __future__ import annotations

import re

PROJECT_ID = re.compile(r"g-p-[0-9a-f]{32}", re.I)
CONVERSATION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)


class ProjectMembershipUnsupported(NotImplementedError):
    """Provider omitted the corroborating project ID or a transport cannot inspect it."""


class ProjectMembershipMismatch(ValueError):
    """Provider metadata contradicts the requested conversation/project pair."""


def verify_conversation_membership(history: dict, conversation_id: str, project_id: str) -> dict:
    """Require independently reported backend project ID and a nonempty user turn.

    This is a snapshot, not a guarantee that membership is permanent. An absent
    provider field is unsupported, never inferred from the current page URL.
    """
    if not isinstance(history, dict):
        raise ProjectMembershipUnsupported("Provider conversation metadata is not an object")
    if not CONVERSATION_ID.fullmatch(conversation_id) or not PROJECT_ID.fullmatch(project_id):
        raise ValueError("Invalid conversation/project ID")
    if history.get("error"):
        raise RuntimeError("Provider history unavailable")
    provider_cid = history.get("provider_conversation_id")
    if not isinstance(provider_cid, str) or not CONVERSATION_ID.fullmatch(provider_cid):
        raise ProjectMembershipUnsupported("Provider conversation identity unavailable")
    if provider_cid != conversation_id:
        raise ProjectMembershipMismatch("Provider returned a different conversation")
    if history.get("conversation_id") != conversation_id:
        raise ProjectMembershipMismatch("Provider returned a different conversation")
    backend_project = history.get("project_id")
    if not isinstance(backend_project, str) or not PROJECT_ID.fullmatch(backend_project):
        raise ProjectMembershipUnsupported("Provider conversation project ID unavailable")
    if backend_project != project_id:
        raise ProjectMembershipMismatch("Provider conversation belongs to another project")
    chain = history.get("chain")
    if not isinstance(chain, list) or not any(
        isinstance(message, dict) and message.get("role") == "user" and message.get("id")
        for message in chain
    ):
        raise ProjectMembershipUnsupported("No identifiable user message in conversation history")
    return {
        "conversation_id": conversation_id,
        "project_id": project_id,
        "verification_method": "provider_conversation_project_id",
    }
