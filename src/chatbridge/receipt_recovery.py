"""Exact authenticated provider user-receipt proof after an uncertain Send.

A complete selected provider history is supplied by BrowserTransport.
This module must NEVER generate, resend or shorten an original prompt.
"""
from __future__ import annotations

import math
import re
from typing import Any


_CHAT_SELF_URL = re.compile(
    r"https://chatgpt[.]com/c/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(?![A-Za-z0-9_-])"
)


def same_provider_self_link_rendering(submitted: str, observed: str) -> bool:
    """Byte-exact except ChatGPT wrapping an unformatted own chat URL.

    Only literal canonical ChatGPT conversation URL -> [URL](same URL) is
    accepted. No whitespace stripping, Markdown parsing, semantic text
    rewriting, arbitrary URLs, source modification or size ceiling.
    """
    if not isinstance(submitted, str) or not isinstance(observed, str):
        return False
    if submitted == observed:
        return True
    original_pos = provider_pos = 0
    converted = False
    for match in _CHAT_SELF_URL.finditer(submitted):
        unchanged = submitted[original_pos:match.start()]
        if not observed.startswith(unchanged, provider_pos):
            return False
        provider_pos += len(unchanged)
        url = match.group()
        if observed.startswith(url, provider_pos):
            provider_pos += len(url)
        else:
            # Source Markdown links are not eligible for newly invented
            # provider wrapping; only bare URLs may acquire self-link markup.
            before = submitted[match.start()-1] if match.start() else ""
            after = submitted[match.end():match.end()+1]
            if before in ("[", "(") or after in ("]", ")"):
                return False
            decorated = f"[{url}]({url})"
            if not observed.startswith(decorated, provider_pos):
                return False
            provider_pos += len(decorated)
            converted = True
        original_pos = match.end()
    return converted and observed[provider_pos:] == submitted[original_pos:]


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
               if node.get("role") == "user"
               and same_provider_self_link_rendering(prompt, node.get("content"))]
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
