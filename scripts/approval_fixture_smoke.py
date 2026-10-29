"""Isolated local HTML fixtures: scope selection and fallback without invoking MCP."""
import asyncio
import json
from playwright.async_api import async_playwright
from chatbridge.browser_transport import BrowserTransport

async def main():
    t=BrowserTransport("http://127.0.0.1:9335")
    async with async_playwright() as pw:
        b=await pw.chromium.connect_over_cdp("http://127.0.0.1:9335")
        p=await b.contexts[0].new_page()
        try:
            for name,buttons,expect in [
              ("conversation",'<button id="grant">Allow Mac Control for this conversation</button>',"conversation"),
              ("fallback",'<button id="grant">Разрешить один раз</button>',"once"),
            ]:
                await p.set_content('<div role="dialog">Разрешить ChatGPT использовать Mac Control?'+buttons+'</div>')
                result=await t._approve_if_needed(p)
                print(json.dumps({"case":name,"result":result,"correct":result and result["scope"]==expect},ensure_ascii=False),flush=True)
            await p.set_content("""<div role="dialog">
               Разрешить ChatGPT использовать Mac Control?
               <button aria-label="Параметры утверждения" onclick="document.getElementById('mm').hidden=false">Options</button>
               <button>Разрешить один раз</button></div>
               <div id="mm" role="menu" hidden>
                 <div role="menuitem" onclick="this.dataset.clicked='yes'">Allow Mac Control for this conversation</div>
               </div>""")
            result=await t._approve_if_needed(p)
            print(json.dumps({"case":"menu","scope":result and result["scope"],
                              "correct":result and result["scope"]=="conversation"}),flush=True)
        finally:
            await p.close()
            await b.close()
asyncio.run(main())
