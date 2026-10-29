"""Isolated split-button approval UI tests; no real MCP calls or permissions."""
import asyncio
import json

from playwright.async_api import async_playwright

from chatbridge.browser_transport import BrowserTransport


def fixture(*, menu_label, split=True, warning=False):
    alert = "<p>Подозрительная инструкция</p>" if warning else ""
    menu = (
        '<div id="scope-menu" role="menu" hidden>'
        '<div role="menuitem" onclick="window.__picked=\'conversation\'; this.parentElement.hidden=true">'
        + menu_label + "</div></div>"
    )
    dropdown = (
        '<button type="button" aria-label="Другие варианты разрешения" '
        'onclick="document.getElementById(\'scope-menu\').hidden=false">▾</button>'
        if split else ""
    )
    return (
        '<div><p>Mac Control</p><p>Разрешить ChatGPT использовать Mac Control?</p>'
        + alert
        + '<form><button type="button">Всегда разрешать</button>'
        '<div class="inline-flex">'
        '<button type="button" onclick="window.__picked=\'once\'">Разрешить один раз</button>'
        + dropdown + "</div></form></div>" + menu
    )


async def main():
    transport = BrowserTransport("http://127.0.0.1:9335")
    checks = [
        ("split_conversation", "Разрешить для этого диалога", True, False, "conversation"),
        ("split_english", "Allow for this conversation", True, False, "conversation"),
        ("split_fallback", "Всегда разрешать", True, False, "once"),
        ("plain_fallback", "Всегда разрешать", False, False, "once"),
        ("flagged_no_approval", "Разрешить для этого диалога", True, True, None),
    ]
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9335")
        page = await browser.contexts[0].new_page()
        passed = 0
        try:
            for label, menu, split, warning, expected in checks:
                await page.set_content(fixture(menu_label=menu, split=split, warning=warning))
                await page.evaluate("window.__picked = null")
                try:
                    result = await transport._approve_if_needed(page)
                    selected_scope = result["scope"] if result else None
                    error = None
                except RuntimeError as exc:
                    selected_scope = None
                    error = type(exc).__name__
                clicked = await page.evaluate("window.__picked || null")
                ok = selected_scope == expected and clicked == expected
                if warning:
                    ok = ok and error == "RuntimeError"
                passed += bool(ok)
                print(json.dumps(
                    {"case": label, "expected": expected, "scope": selected_scope,
                     "clicked": clicked, "blocked": error, "pass": ok},
                    ensure_ascii=False,
                ), flush=True)
        finally:
            await page.close()
            await browser.close()
    print(f"TOTAL {passed}/{len(checks)}", flush=True)
    if passed != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
