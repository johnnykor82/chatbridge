"""BF30: search-first, privacy-safe timed diagnostic phases; offline only."""
import logging

import pytest

from chatbridge import marker_lookup
from chatbridge.rate_limit import ProviderRateLimitError

MARKER = "WC9d4f8a2c7b61e305"
DESTINATION = "https://chatgpt.com/c/11111111-1111-4111-8111-111111111111"
VERIFIED = {"chat_url": DESTINATION, "evidence_verified": True,
            "verification": "assistant_message", "matches": 1}


@pytest.mark.asyncio
async def test_search_first_success_reports_source_and_timing(monkeypatch, caplog):
    calls = []

    async def search(*args, **kwargs):
        calls.append("search")
        return VERIFIED

    async def recent(*args, **kwargs):
        calls.append("recent")
        raise AssertionError("unnecessary recent request")

    monkeypatch.setattr(marker_lookup, "resolve_in_browser", search)
    monkeypatch.setattr(marker_lookup, "resolve_in_recent", recent)
    with caplog.at_level(logging.INFO, logger=marker_lookup.__name__):
        result = await marker_lookup.resolve_automatically(object(), MARKER)
    assert result == VERIFIED
    assert calls == ["search"]
    joined = "\n".join(r.message for r in caplog.records if "lookup" in r.message.lower())
    assert "stage=total" in joined
    assert "source=search_index" in joined
    assert "elapsed_ms=" in joined
    assert MARKER not in joined
    assert DESTINATION not in joined


@pytest.mark.asyncio
async def test_search_miss_recent_fallback_source_and_timing(monkeypatch, caplog):
    calls = []

    async def search(*args, **kwargs):
        calls.append("search")
        raise marker_lookup.MarkerNotFound("not indexed")

    async def recent(*args, **kwargs):
        calls.append("recent")
        return VERIFIED

    monkeypatch.setattr(marker_lookup, "resolve_in_browser", search)
    monkeypatch.setattr(marker_lookup, "resolve_in_recent", recent)
    with caplog.at_level(logging.INFO, logger=marker_lookup.__name__):
        assert await marker_lookup.resolve_automatically(object(), MARKER) == VERIFIED
    assert calls == ["search", "recent"]
    logs = "\n".join(r.message for r in caplog.records)
    assert "source=recent" in logs
    assert "stage=search_total" in logs
    assert "stage=recent_total" in logs
    assert "stage=total" in logs
    assert MARKER not in logs


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    ProviderRateLimitError("private marker " + MARKER),
    PermissionError("private marker " + MARKER),
    marker_lookup.AmbiguousMarker("private marker " + MARKER),
    RuntimeError("Chat history verification unavailable: private " + MARKER),
])
async def test_nonrecoverable_search_fails_closed_and_logs_no_secret(
    monkeypatch, caplog, failure,
):
    async def search(*args, **kwargs):
        raise failure

    async def recent(*args, **kwargs):
        raise AssertionError("must not fall back on protected failures")

    monkeypatch.setattr(marker_lookup, "resolve_in_browser", search)
    monkeypatch.setattr(marker_lookup, "resolve_in_recent", recent)
    with caplog.at_level(logging.INFO, logger=marker_lookup.__name__), pytest.raises(type(failure)):
        await marker_lookup.resolve_automatically(object(), MARKER)
    joined = "\n".join(r.message for r in caplog.records)
    assert MARKER not in joined
    assert "stage=total" in joined


@pytest.mark.asyncio
async def test_recoverable_search_ui_failure_uses_recent(monkeypatch):
    calls = []

    async def search(*args, **kwargs):
        calls.append("search")
        raise RuntimeError("ChatGPT search dialog input unavailable")

    async def recent(*args, **kwargs):
        calls.append("recent")
        return VERIFIED

    monkeypatch.setattr(marker_lookup, "resolve_in_browser", search)
    monkeypatch.setattr(marker_lookup, "resolve_in_recent", recent)
    assert await marker_lookup.resolve_automatically(object(), MARKER) == VERIFIED
    assert calls == ["search", "recent"]
