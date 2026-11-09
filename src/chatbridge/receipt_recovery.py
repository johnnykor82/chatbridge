"""Exact authenticated provider user-receipt proof after an uncertain Send.

A complete selected provider history is supplied by BrowserTransport.
This module must NEVER generate, resend or shorten an original prompt.
"""
from __future__ import annotations

import math
from typing import Any


def exact_user_receipt(
    chain: list[dict],
    *,
    prompt: str,
    request_created_at: float,
    expected_parent_id: str | None = None,
) -> dict[str, Any] | None:
    """Return only an unambiguous original provider user message.

    Provider-created timestamp precedes no accepted local submission by more
    than 300s of clock skew. No upper bound: outages may last indefinitely.
    """
    if not isinstance(chain, list) or not chain:
        return None
    if not isinstance(prompt, str):
        return None
    ids = []
    for node in chain:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            return None
        ids.append(node["id"])
    if len(set(ids)) != len(ids):
        return None
    # The provider traversal must be an actual selected message parent chain.
    for newer, older in zip(chain, chain[1:]):
        parent = newer.get("branch_parent_id", newer.get("parent_id"))
        if ("branch_parent_id" in newer or "parent_id" in newer) and parent != older["id"]:
            return None

    matches = [node for node in chain
               if node.get("role") == "user" and node.get("content") == prompt]
    if len(matches) != 1:
        return None
    candidate = matches[0]
    if expected_parent_id is not None and candidate.get("parent_id") != expected_parent_id:
        return None
    raw_time = candidate.get("create_time")
    try:
        created = float(raw_time)
        started = float(request_created_at)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(created) or not math.isfinite(started)
            or created < started - 300):
        return None
    return candidate
