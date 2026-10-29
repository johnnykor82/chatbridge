"""Browser UI diagnostic for ChatBridge model picker, no message submission.
Uses background CDP tab, does not log credentials or patient data.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    port=int(Path.home().joinpath('.chatbridge/chrome-profile/DevToolsActivePort').read_text().splitlines()[0])
    async with async_playwright() as p:
        b=await p.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=12000)
        ctx=b.contexts[0]
        session=await b.new_browser_cdp_session()
        try:
            async with ctx.expect_page(timeout=18000) as ev:
                await session.send('Target.createTarget',{'url':'about:blank','background':True,'focus':False})
            page=await ev.value
        finally:
            await session.detach()
        try:
            # Authenticated home in this independent Bridge profile.
            response=await page.goto('https://chatgpt.com/',wait_until='domcontentloaded',timeout=35000)
            await page.wait_for_timeout(3500)
            print('NAV',{'http':response.status if response else None,'is_chatgpt':page.url.startswith('https://chatgpt.com/'),'title_present':bool(await page.title())})
            found=await page.evaluate('''() => ({
             buttons:[...document.querySelectorAll('button')].filter(x=>/Select ChatGPT model|Выбрать модель ChatGPT/i.test(x.getAttribute('aria-label')||'')).map(x=>({label:x.getAttribute('aria-label'),visible:!!(x.offsetWidth||x.offsetHeight||x.getClientRects().length),reasoning:x.getAttribute('data-selected-reasoning-effort'),outerTag:x.tagName})).slice(0,5),
             sliders:document.querySelectorAll('[data-reasoning-slider]').length,
             editors:document.querySelectorAll('div[contenteditable=true].ProseMirror').length,
             presentUIBlockers:['Verify you are human','Проверяем','Sign in','Войти','Too many requests','Слишком много запросов'].filter(x=>(document.body?.innerText||'').includes(x))
            })''')
            print('DOM',found)
            picker=page.get_by_role('button',name=__import__('re').compile(r'(Select ChatGPT model|Выбрать модель ChatGPT)',__import__('re').I))
            try:
                await picker.first.wait_for(state='visible',timeout=6000)
                print('PICKER_VISIBLE',True)
                await picker.first.evaluate('(el) => el.click()')
                await page.wait_for_timeout(300)
                slider=page.locator('[data-reasoning-slider]').first
                print('SLIDER_VISIBLE',await slider.is_visible())
                print('MENU_ACCESSIBLE_OPTIONS',await page.get_by_role('menuitem').count())
                if await slider.is_visible():
                    knob=slider.locator('[aria-valuenow]').first
                    print('SLIDER_VALUE_BEFORE',await knob.get_attribute('aria-valuenow'))
                    await slider.focus()
                    await slider.press('ArrowRight')
                    await page.wait_for_timeout(280)
                    print('SLIDER_VALUE_AFTER_RIGHT',await knob.get_attribute('aria-valuenow'))
                await page.keyboard.press('Escape')
            except Exception as e:
                print('PICKER_ERROR',type(e).__name__,str(e)[-1100:])
                print('BLOCKERS',await page.evaluate('''() => {
                    const x=document.querySelector('button[aria-label="Выбрать модель ChatGPT"]');
                    if(!x)return {missing:true};
                    const r=x.getBoundingClientRect(), y=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                    return {buttonRect:[r.x,r.y,r.width,r.height],atCenter:y?.outerHTML?.slice(0,250),
                      activeTag:document.activeElement?.tagName,documentVisibility:document.visibilityState,
                      blockers:[...document.querySelectorAll('[role=dialog],[data-state=open]')].map(x=>(x.getAttribute('role')||x.tagName)).slice(0,8)}
                }'''))
                await picker.first.evaluate('(el) => el.click()')
                await page.wait_for_timeout(500)
                print('DOM_CLICK_SLIDER',await page.locator('[data-reasoning-slider]').first.is_visible())
                print('DOM_CLICK_OPTIONS',await page.get_by_role('menuitem').count())
        finally:
            await page.close()
        # No browser disconnect: ChatBridge owns the CDP browser.

asyncio.run(main())
