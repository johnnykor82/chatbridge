"""Heuristics for minimizing authenticated ChatGPT history reads.

No assumption about model tokens/sec is used as evidence of a finished turn.
Provider backend's final end_turn remains the only completion authority.
"""

from __future__ import annotations

from dataclasses import dataclass


def first_probe_delay(
    prompt_chars: int,
    context_chars: int = 0,
    history_messages: int = 0,
    attachments: int = 0,
    effort: str = "high",
) -> float:
    """Bounded time to first *fallback* backend read without a UI completion signal."""
    chars = max(0, prompt_chars) + max(0, context_chars)
    # Only a rough text-size proxy: real model context includes hidden instructions,
    # image/audio content and project memory unavailable to this transport.
    delay = (
        1.7
        + min(11.0, chars / 1400.0)
        + min(5.0, max(0, history_messages) / 20.0)
        + min(5.0, max(0, attachments) * 2.5)
        + (1.5 if effort == "high" else 0.5 if effort == "medium" else 0.0)
    )
    return round(min(24.0, max(2.0, delay)), 1)


@dataclass
class VerificationSchedule:
    """An early finality signal triggers *one* read; missing signals use backoff."""

    started_at: float
    initial_delay: float
    probes: int = 0
    next_allowed: float = 0.0
    next_fallback: float = 0.0

    def __post_init__(self):
        self.next_allowed = self.started_at + 1.0
        self.next_fallback = self.started_at + self.initial_delay

    def should_check(
        self, now: float, *, likely_finished: bool, actively_generating: bool = False
    ) -> bool:
        if now < self.next_allowed:
            return False
        # The stop-generation UI is a *local* signal: no reason to poll
        # backend while a known turn is still active. A low-rate 90s
        # fallback remains for stuck or out-of-sync browser UI.
        if actively_generating and not likely_finished and now - self.started_at < 90:
            return False
        return likely_finished or now >= self.next_fallback

    def checked(self, now: float, *, rate_limited: bool = False, retry_after: float = 0) -> None:
        self.probes += 1
        if rate_limited:
            wait = max(60.0, retry_after)
        else:
            wait = min(120.0, 12.0 * 2 ** min(4, self.probes - 1))
        # Persistent SSE completion markers must not create a tight read loop.
        self.next_allowed = now + wait
        self.next_fallback = now + max(30.0, wait)
