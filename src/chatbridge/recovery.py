"""Client-facing recovery hints; never authorize retransmission of unknown turns."""

from __future__ import annotations

import math
import time


def recovery_hints(row: dict, rate_limit: dict, *, now: float | None = None) -> dict:
    """Deterministic advice derived from durable request and provider cooldown state."""
    now = time.time() if now is None else now
    retry_after = max(0, int(math.ceil(rate_limit.get("retry_after") or 0)))
    status = row["status"]
    if status == "unknown":
        if not row.get("user_message_id"):
            reason = "submission_unconfirmed"
        elif not row.get("conversation_id"):
            reason = "conversation_unbound"
        else:
            reason = "provider_end_turn_unconfirmed"
        if rate_limit.get("provider_rate_limited"):
            reason = "provider_rate_limited"
        # Reconcile is read-only; it must not be confused with a retry of POST.
        # Stable across status polls: a moving now+30 timestamp would prevent
        # clients from ever reaching the recommended reconciliation instant.
        earliest = float(row.get("updated_at") or now) + 30
        if rate_limit.get("provider_rate_limited"):
            earliest = max(earliest, float(rate_limit.get("paused_until") or now + retry_after))
        return {
            "retry_after": retry_after,
            "unknown_reason": reason,
            "reconcile_after": earliest if row.get("user_message_id") and row.get("conversation_id") else None,
        }
    return {
        "retry_after": retry_after if status == "queued" else 0,
        "unknown_reason": None,
        "reconcile_after": None,
    }
