"""Completion signals save provider reads; heuristics never certify end_turn."""

from chatbridge.poll_policy import VerificationSchedule, first_probe_delay


def test_small_chat_does_not_wait_for_arbitrary_long_context_timeout():
    small = first_probe_delay(14, 0, 0, 0, "low")
    medium = first_probe_delay(4800, 15000, 20, 0, "medium")
    huge = first_probe_delay(50000, 250000, 200, 3, "high")
    assert 2 <= small <= 4
    assert small < medium < huge <= 24


def test_early_sse_completion_overrides_initial_prefill_delay():
    policy = VerificationSchedule(started_at=100.0, initial_delay=22.0)
    assert not policy.should_check(102.0, likely_finished=False)
    assert policy.should_check(103.0, likely_finished=True)
    policy.checked(103.0)
    assert not policy.should_check(105.0, likely_finished=True)
    assert policy.should_check(116.0, likely_finished=True)


def test_missing_sse_has_safe_fallback_and_backoff():
    policy = VerificationSchedule(started_at=0.0, initial_delay=3.0)
    assert not policy.should_check(2.5, likely_finished=False)
    assert policy.should_check(3.0, likely_finished=False)
    policy.checked(3.0)
    assert not policy.should_check(22.0, likely_finished=False)
    assert policy.should_check(33.0, likely_finished=False)
    policy.checked(33.0)
    assert not policy.should_check(50.0, likely_finished=True)
    assert policy.should_check(57.0, likely_finished=True)


def test_provider_429_defers_confirmations_without_fanout():
    policy = VerificationSchedule(started_at=0.0, initial_delay=2.0)
    policy.checked(2.0, rate_limited=True, retry_after=90.0)
    assert not policy.should_check(35.0, likely_finished=True)
    assert policy.should_check(93.0, likely_finished=True)


def test_active_generation_skips_early_history_fetches():
    policy = VerificationSchedule(started_at=0.0, initial_delay=22.0)
    assert not policy.should_check(23.0, likely_finished=False, actively_generating=True)
    assert not policy.should_check(70.0, likely_finished=False, actively_generating=True)
    assert policy.should_check(70.0, likely_finished=True, actively_generating=True)
    assert policy.should_check(91.0, likely_finished=False, actively_generating=True)
