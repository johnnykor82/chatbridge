"""CDP authenticated no-send regression for native reasoning picker."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from chatbridge.browser_transport import BrowserTransport

async def main():
    p=Path.home()/'.chatbridge/chrome-profile/DevToolsActivePort'
    port=p.read_text().splitlines()[0]
    async with async_playwright() as pw:
        browser=await pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}',timeout=15000)
        context=browser.contexts[0]
        session=await browser.new_browser_cdp_session()
        try:
            async with context.expect_page(timeout=18000) as event:
                await session.send('Target.createTarget',{'url':'about:blank','background':True,'focus':False})
            page=await event.value
        finally:await session.detach()
        try:
            response=await page.goto('https://chatgpt.com/',wait_until='domcontentloaded',timeout=35000)
            assert response and response.status==200
            transport=BrowserTransport(f'http://127.0.0.1:{port}',reasoning_effort='high',visible_background=True)
            await transport._configure_reasoning_effort(page,'high')
            state=await page.evaluate('''() => ({
                editor:!!document.querySelector('div[contenteditable=true].ProseMirror'),
                actualEffort:document.querySelector('button[data-composer-navigation-target=reasoning]')?.getAttribute('data-selected-reasoning-effort'),
                sendNotTriggered:!document.querySelector('button[aria-label="Stop streaming"]')
            })''')
            print('PICKER_REGRESSION_PASS',state)
        finally:
            await page.close()

asyncio.run(main())
