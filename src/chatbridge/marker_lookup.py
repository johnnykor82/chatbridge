"""Resolve a ChatGPT conversation by a user-posted, high-entropy text marker.

Search happens in ChatBridge's already-owned authenticated browser context.
No direct ChatGPT credentials or private endpoints are used by the Watcher.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

MARKER = re.compile(r"WATCHER-CHAT-[a-f0-9]{32}\Z")
CHAT = re.compile(r"/c/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z", re.I)
PROJECT = re.compile(
    r"/g/(g-p-[0-9a-f]{32})(?:-[a-z0-9-]+)?/c/"
    r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z", re.I
)


class MarkerNotFound(LookupError):
    pass


class AmbiguousMarker(ValueError):
    pass


def validate_marker(value):
    if not isinstance(value, str) or not MARKER.fullmatch(value):
        raise ValueError("Expected WATCHER-CHAT- followed by exactly 32 lowercase hex characters")
    return value


def canonical_candidate(url):
    """Normalize only actual conversation results from the trusted ChatGPT origin."""
    if isinstance(url, str) and url.startswith("/"):
        url = "https://chatgpt.com" + url
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.netloc != "chatgpt.com"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        return None
    m = PROJECT.fullmatch(parsed.path)
    if m:
        return f"https://chatgpt.com/g/{m.group(1).lower()}/c/{m.group(2).lower()}"
    m = CHAT.fullmatch(parsed.path)
    if m:
        return f"https://chatgpt.com/c/{m.group(1).lower()}"
    return None


def message_contains_marker(text, marker):
    # No substring matching a suffix/prefix or an assistant response.
    return bool(re.search(r"(?<![A-Za-z0-9-])" + re.escape(marker) + r"(?![A-Za-z0-9-])", text))


user_message_contains_marker = message_contains_marker


async def resolve_in_browser(browser, marker, observe=None, new_page=None):
    """Search the built-in UI, verify exact USER message in each candidate.

    Search has a hard candidate cap; ambiguous matches fail closed. A transient
    UI issue is not evidence that a chat does not exist.
    """
    validate_marker(marker)
    if not browser.contexts:
        raise RuntimeError("No authenticated ChatGPT browser context")
    context = browser.contexts[0]
    page = await new_page(browser) if new_page else await context.new_page()
    found = {}
    try:
        if observe:
            page.on("response", observe)
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
        if urlsplit(page.url).netloc != "chatgpt.com":
            raise RuntimeError("ChatGPT session is not authenticated")
        buttons = [
            page.get_by_role("button", name=re.compile(r"search chats|поиск чатов|^поиск$|^search$", re.I)),
            page.locator('[data-testid="search-chats-button"]'),
            page.locator('[data-testid="sidebar-search-button"]'),
        ]
        for button in buttons:
            if await button.count() and await button.first.is_visible():
                await button.first.click(timeout=4500)
                break
        else:
            raise RuntimeError("ChatGPT search control not found; UI changed")
        # Search UI animates into place; checking count immediately after
        # clicking is a race (observed on the real ChatGPT website).
        await page.wait_for_timeout(1000)
        fields = [
            page.get_by_role("searchbox"),
            page.get_by_placeholder(re.compile(r"search|поиск", re.I)),
            page.locator('[role="dialog"] input'),
            page.locator('input[role="combobox"]'),
            page.locator('input[type="search"]'),
        ]
        for field in fields:
            if await field.count() and await field.first.is_visible():
                await field.first.fill(marker, timeout=5000)
                break
        else:
            raise RuntimeError("ChatGPT search input not found; UI changed")
        await page.wait_for_timeout(2200)
        # UI can debounce the search, but never retry the HTTP query in a tight loop.
        dialogs = page.locator('[role="dialog"]')
        if not await dialogs.count():
            raise RuntimeError("ChatGPT search results dialog is unavailable")
        links = dialogs.last.locator('a[href^="/c/"], a[href^="/g/"]')
        for _ in range(8):
            if await links.count():
                break
            await page.wait_for_timeout(850)
        count = await links.count()
        if count == 0:
            # ChatGPT Search indexes standalone terms more consistently than
            # punctuation-heavy markers. Search the high-entropy 32-hex part
            # as a bounded fallback; still verify the full marker in the chat.
            await field.first.fill(marker.rsplit("-", 1)[-1], timeout=5000)
            await page.wait_for_timeout(3600)
            count = await links.count()
        if count == 0:
            # Diagnostic metadata only: never log the search term or chat text.
            import logging
            attributes = await page.locator('[role="dialog"]').last.evaluate("""el =>
                Array.from(el.querySelectorAll('a, [role=option], input')).slice(0,12)
                .map(n => ({tag:n.tagName,role:n.getAttribute('role'),
                  href:n.getAttribute('href'),type:n.getAttribute('type')}))""")
            logging.getLogger(__name__).warning(
                "Marker search no results; current_url=%s; children=%s",
                page.url.split("?", 1)[0],
                [{k:(v if k!='href' or v is None else bool(v)) for k,v in a.items()}
                 for a in attributes],
            )
        if count > 5:
            raise AmbiguousMarker("More than five results; narrow down the marker")
        candidates = []
        for i in range(count):
            href = await links.nth(i).get_attribute("href")
            # Search UI links may contain its own tracking parameters;
            # strip them before canonical validation, never for user API input.
            href = href.split("#", 1)[0].split("?", 1)[0] if href else href
            target = canonical_candidate(href)
            if target and target not in candidates:
                candidates.append(target)
        if not candidates:
            raise MarkerNotFound("No ChatGPT search results for this marker")
        for target in candidates:
            # The URL in search results is only a candidate, never proof.
            probe = await new_page(browser) if new_page else await context.new_page()
            try:
                if observe:
                    probe.on("response", observe)
                await probe.goto(target, wait_until="domcontentloaded", timeout=30000)
                if canonical_candidate(probe.url) != target:
                    continue
                user = probe.locator(
                    '[data-user-message-bubble="true"], [data-message-author-role="user"]'
                )
                assistant = probe.locator(
                    '[data-message-author-role="assistant"], [data-testid="conversation-turn-assistant"]'
                )
                for _ in range(12):
                    if await user.count() or await assistant.count():
                        break
                    await probe.wait_for_timeout(700)
                role = None
                if any(message_contains_marker(t, marker) for t in await assistant.all_inner_texts()):
                    role = "assistant_message"
                elif any(message_contains_marker(t, marker) for t in await user.all_inner_texts()):
                    role = "user_message"
                if not role:
                    # ChatGPT may virtualize an assistant turn or render
                    # structured rich content outside the usual message node.
                    # Read the provider's CURRENT selected conversation chain,
                    # require an exact conversation ID, and check explicit role.
                    from .browser_transport import BACKEND_JS
                    expected_cid = target.rsplit("/c/", 1)[-1]
                    history = await probe.evaluate(BACKEND_JS, expected_cid)
                    if history.get("error") == "history_http_429":
                        from .rate_limit import ProviderRateLimitError
                        raise ProviderRateLimitError("ChatGPT history search rate limited")
                    if "error" in history:
                        raise RuntimeError("Chat history verification unavailable")
                    if history.get("conversation_id") != expected_cid:
                        raise RuntimeError("Provider conversation identity mismatch")
                    roles = {
                        node.get("role") for node in history.get("chain", [])
                        if node.get("role") in ("user", "assistant") and message_contains_marker(
                            node.get("content", ""), marker
                        )
                    }
                    if "assistant" in roles:
                        role = "assistant_message"
                    elif "user" in roles:
                        role = "user_message"
                if role:
                    found[target] = role
            finally:
                await probe.close()
        unique = sorted(found)
        if len(unique) > 1:
            raise AmbiguousMarker("Marker appears in more than one verified conversation")
        if not unique:
            raise MarkerNotFound("No exact user or assistant message marker could be verified")
        return {"chat_url": unique[0], "evidence_verified": True,
                "verification": found[unique[0]], "matches": 1}
    finally:
        await page.close()


# Fallback for delayed/unavailable ChatGPT full-text search indexing.
# Recent sidebar links are *candidates only*. Full marker+role and provider
# conversation identity are independently verified before a URL is returned.
def recent_chat_candidates(hrefs, *, limit=8):
    if not isinstance(limit, int) or not 1 <= limit <= 24:
        raise ValueError("Recent conversation scan limit must be 1..24")
    found = []
    for href in hrefs:
        candidate = canonical_candidate(href)
        if candidate and candidate not in found:
            found.append(candidate)
        if len(found) >= limit:
            break
    return found


async def enumerate_recent_chat_urls(browser, *, new_page=None):
    """Read bounded links from the authenticated homepage sidebar; no search index."""
    from urllib.parse import urlsplit
    if not browser.contexts:
        raise RuntimeError("No authenticated ChatGPT browser context")
    page = await new_page(browser) if new_page else await browser.contexts[0].new_page()
    try:
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=20000)
        if urlsplit(page.url).netloc != "chatgpt.com":
            raise RuntimeError("ChatGPT session is not authenticated")
        locator = page.locator('a[href^="/c/"], a[href^="/g/"]')
        for _ in range(5):
            if await locator.count():
                break
            await page.wait_for_timeout(550)
        hrefs = []
        for i in range(min(await locator.count(), 36)):
            hrefs.append(await locator.nth(i).get_attribute("href"))
        return recent_chat_candidates(hrefs, limit=8)
    finally:
        await page.close()


async def verify_candidate_chat(browser, marker, target, *, observe=None, new_page=None):
    """Verify a candidate against its exact ChatGPT conversation and full marker."""
    validate_marker(marker)
    target = canonical_candidate(target)
    if not target:
        raise ValueError("Candidate is not a canonical ChatGPT conversation")
    if not browser.contexts:
        raise RuntimeError("No authenticated ChatGPT browser context")
    probe = await new_page(browser) if new_page else await browser.contexts[0].new_page()
    try:
        if observe:
            probe.on("response", observe)
        await probe.goto(target, wait_until="domcontentloaded", timeout=15000)
        if canonical_candidate(probe.url) != target:
            return None
        user = probe.locator('[data-user-message-bubble="true"], [data-message-author-role="user"]')
        assistant = probe.locator(
            '[data-message-author-role="assistant"], [data-testid="conversation-turn-assistant"]')
        for _ in range(7):
            if await user.count() or await assistant.count():
                break
            await probe.wait_for_timeout(450)
        if any(message_contains_marker(t, marker) for t in await assistant.all_inner_texts()):
            return "assistant_message"
        if any(message_contains_marker(t, marker) for t in await user.all_inner_texts()):
            return "user_message"
        # Existing ChatBridge provider-chain verification supports virtualized
        # messages but never accepts an unverified conversation ID or role.
        from .browser_transport import BACKEND_JS
        expected_cid = target.rsplit("/c/", 1)[-1]
        history = await probe.evaluate(BACKEND_JS, expected_cid)
        if history.get("error") == "history_http_429":
            from .rate_limit import ProviderRateLimitError
            raise ProviderRateLimitError("ChatGPT history search rate limited")
        if "error" in history:
            raise RuntimeError("Chat history verification unavailable")
        if history.get("conversation_id") != expected_cid:
            raise RuntimeError("Provider conversation identity mismatch")
        for node in history.get("chain", []):
            if node.get("role") in ("user", "assistant") and message_contains_marker(
                node.get("content", ""), marker
            ):
                return node["role"] + "_message"
        return None
    finally:
        await probe.close()



class RecentScanUnavailable(RuntimeError):
    """Recent sidebar or provider history could not be verified."""


def verified_role_from_history(history, expected_cid, marker):
    """Verify real provider conversation identity and full marker author role."""
    validate_marker(marker)
    if not isinstance(history, dict):
        raise RecentScanUnavailable("History verification unavailable")
    if history.get("error") == "history_http_429":
        from .rate_limit import ProviderRateLimitError
        raise ProviderRateLimitError("Provider history rate limited")
    if history.get("error") in ("not_authenticated", "history_http_401", "history_http_403"):
        raise PermissionError("ChatGPT history authorization denied")
    if history.get("error") == "history_http_404":
        return None
    if history.get("error"):
        raise RecentScanUnavailable("Provider history lookup unavailable")
    if history.get("provider_conversation_id") != expected_cid:
        raise RecentScanUnavailable("Provider conversation identity mismatch")
    chain = history.get("chain")
    if not isinstance(chain, list):
        raise RecentScanUnavailable("Provider conversation chain missing")
    roles = {
        node.get("role") for node in chain
        if isinstance(node, dict) and message_contains_marker(
            node.get("content", ""), marker
        )
    }
    if "assistant" in roles:
        return "assistant_message"
    if "user" in roles:
        return "user_message"
    return None




RECENT_LIST_JS = r"""async () => {
  try {
    const session = await fetch('/api/auth/session', {credentials:'include'})
      .then(r => r.json());
    if (!session?.accessToken) return {error:'not_authenticated'};
    const response = await fetch(
      '/backend-api/conversations?offset=0&limit=28&order=updated', {
      credentials:'include', cache:'no-store',
      headers:{authorization:'Bearer '+session.accessToken}
    });
    if (!response.ok) return {
      error:'recent_http_'+response.status,
      retry_after:response.headers.get('retry-after')
    };
    const payload=await response.json();
    const rows=Array.isArray(payload.items) ? payload.items :
      (Array.isArray(payload.conversations) ? payload.conversations : null);
    if (!rows) return {error:'malformed_recent_list'};
    return {ids:rows.slice(0,28).map(x=>x.id||x.conversation_id||null)};
  } catch(e) { return {error:'recent_exception_'+e.name}; }
}"""


def provider_recent_candidates(data, *, limit=12):
    """Accept only UUID conversation IDs, never caller-supplied destinations."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Expected provider conversation items list")
    if not isinstance(limit, int) or not 1 <= limit <= 24:
        raise ValueError("Invalid recent provider candidate limit")
    urls = []
    for item in data["items"][:40]:
        if not isinstance(item, dict):
            continue
        ident = item.get("id") or item.get("conversation_id")
        if not isinstance(ident, str):
            continue
        candidate = canonical_candidate("https://chatgpt.com/c/" + ident)
        if candidate and candidate not in urls:
            urls.append(candidate)
        if len(urls) >= limit:
            break
    return urls


async def provider_recent_chat_urls(page):
    """At most one read-only recent index fetch from existing Chrome context."""
    import asyncio

    result = await asyncio.wait_for(page.evaluate(RECENT_LIST_JS), timeout=10)
    if not isinstance(result, dict):
        raise RecentScanUnavailable("Recent list response invalid")
    error = result.get("error", "")
    if error == "recent_http_429":
        from .rate_limit import ProviderRateLimitError
        raise ProviderRateLimitError("Provider recent conversation list rate limited")
    if error in ("not_authenticated", "recent_http_401", "recent_http_403"):
        raise PermissionError("Recent conversation listing unauthorized")
    if error:
        raise RecentScanUnavailable("Recent conversation list unavailable")
    return provider_recent_candidates(
        {"items": [{"id": cid} for cid in result.get("ids", [])]}, limit=12
    )


async def resolve_recent_page(page, marker):
    """Verify recent ChatGPT sidebar links on ONE authenticated page."""
    import asyncio

    from .browser_transport import BACKEND_JS

    locator = page.locator('a[href^="/c/"], a[href^="/g/"]')
    for _ in range(4):
        if await locator.count():
            break
        await page.wait_for_timeout(500)
    hrefs = []
    for i in range(min(await locator.count(), 48)):
        hrefs.append(await locator.nth(i).get_attribute("href"))
    candidates = recent_chat_candidates(hrefs, limit=12)
    found = {}
    scanned = set()
    incomplete = 0

    async def scan(urls):
        nonlocal incomplete
        # Check a small current-recency group for duplicate verified markers.
        # Once the high-entropy marker is verified in an assistant message,
        # unrelated older chat histories are unnecessary; a later history
        # timeout must not erase a verified destination.
        for group_start in range(0, len(urls), 3):
            for candidate in urls[group_start:group_start + 3]:
                if candidate in scanned or len(scanned) >= 20:
                    continue
                scanned.add(candidate)
                expected_cid = candidate.rsplit("/c/", 1)[-1]
                try:
                    history = await asyncio.wait_for(
                        page.evaluate(BACKEND_JS, expected_cid), timeout=6
                    )
                    role = verified_role_from_history(history, expected_cid, marker)
                except (RecentScanUnavailable, TimeoutError):
                    incomplete += 1
                    continue
                if role:
                    found[candidate] = role
            if len(found) > 1:
                raise AmbiguousMarker("Marker appears in multiple verified recent chats")
            if any(role == "assistant_message" for role in found.values()):
                return
    await scan(candidates)
    if not found and len(scanned) < 20:
        try:
            provider_candidates = await provider_recent_chat_urls(page)
        except RecentScanUnavailable:
            incomplete += 1
            provider_candidates = []
        await scan(provider_candidates)
    import logging
    logging.getLogger(__name__).info(
        "Recent marker discovery sidebar_candidates=%d verified_candidates=%d "
        "history_unavailable=%d inspected=%d",
        len(candidates), len(found), incomplete, len(scanned),
    )
    if len(found) > 1:
        raise AmbiguousMarker("Marker appears in more than one verified chat")
    if incomplete and not any(role == "assistant_message" for role in found.values()):
        raise RecentScanUnavailable("Recent conversation verification incomplete")
    if not found:
        raise MarkerNotFound("Exact marker absent from verified recent conversations")
    chat_url, role = next(iter(found.items()))
    return {"chat_url": chat_url, "evidence_verified": True,
            "verification": role, "matches": 1}


async def resolve_in_recent(browser, marker, observe=None, new_page=None):
    """Resolve without ChatGPT search index or repeated candidate navigations."""
    import asyncio
    from urllib.parse import urlsplit

    validate_marker(marker)
    if not browser.contexts:
        raise RecentScanUnavailable("No authenticated ChatGPT browser context")
    page = await new_page(browser) if new_page else await browser.contexts[0].new_page()
    try:
        if observe:
            page.on("response", observe)
        async with asyncio.timeout(85):
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded",
                            timeout=15000)
            if urlsplit(page.url).netloc != "chatgpt.com":
                raise PermissionError("ChatGPT browser not authenticated")
            return await resolve_recent_page(page, marker)
    finally:
        await page.close()

async def resolve_automatically(browser, marker, observe=None, new_page=None):
    """Prefer sidebar to full-text index, retaining one bounded HTTP deadline."""
    import asyncio
    validate_marker(marker)
    async with asyncio.timeout(170):
        try:
            return await resolve_in_recent(browser, marker, observe, new_page)
        except (MarkerNotFound, RecentScanUnavailable, TimeoutError):
            # A failed recent-chat scan is not evidence the chat is missing;
            # search is the bounded secondary discovery strategy.
            return await resolve_in_browser(browser, marker, observe, new_page)
