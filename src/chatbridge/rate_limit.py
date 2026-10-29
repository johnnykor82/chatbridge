"""Provider 429 parsing and a safe marker for never-submitted turns."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class ProviderRateLimitError(RuntimeError):
    """No user message was submitted; postpone until global cooldown expires."""


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float:
    """Accept HTTP Retry-After seconds or HTTP-date, without trusting huge values."""
    if not value:
        return 0.0
    value = str(value).strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - (now or datetime.now(timezone.utc))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0.0
    if seconds != seconds:  # NaN
        return 0.0
    return max(0.0, min(3600.0, seconds))
