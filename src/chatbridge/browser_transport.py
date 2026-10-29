"""CDP browser transport. Browser performs actual requests; no external web POST.

Each active request owns a short-lived Chrome tab. Its same-origin context reads
the complete backend turn chain; no cookies or auth tokens leave the renderer.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from .artifact_export import download_visible_artifacts
from .media import ImagePayload
from .model import TransportEvent
from .poll_policy import VerificationSchedule, first_probe_delay
from .project_evidence import verify_conversation_membership
from .rate_limit import ProviderRateLimitError, retry_after_seconds

log = logging.getLogger(__name__)


class SubmissionUncertainError(RuntimeError):
    """The send was attempted but its delivery cannot yet be established."""


INTERCEPT_JS = r"""(() => {
 if (window.__chatbridgeHooked) return;
 window.__chatbridgeHooked = true;
 window.__chatbridge = {
    requestId: null, parentId: null, responseStatus: null,
    sseCompleted: false, sseFinished: false, sseEvents: 0,
    streamError: null, markers: {}, chunks: 0
 };
 const orig = window.fetch;
 window.fetch = async function(...args) {
   const input = args[0], init = args[1] || {};
   const url = typeof input === 'string' ? input : String(input?.url || '');
   const isTurn = /\/backend-api\/f\/conversation(?:\?|$)/.test(url);
   if (isTurn) {
     try {
       let raw = init.body;
       if (!raw && input instanceof Request) raw = await input.clone().text();
       let body = JSON.parse(raw || '{}');
       let msg = (body.messages || []).find(x => x.author?.role === 'user');
       if (msg) {
         window.__chatbridge.requestId = msg.id || null;
         window.__chatbridge.parentId = body.parent_message_id || null;
       }
     } catch (e) { /* Fail closed in turn verification. */ }
   }
   const response = await orig.apply(this,args);
   if (!isTurn) return response;
   let s = window.__chatbridge;
   s.responseStatus = response.status;
   let copy = response.clone();
   (async () => {
     const decoder = new TextDecoder();
     let buf = '';
     try {
       const reader = copy.body.getReader();
       while (true) {
         let {done,value} = await reader.read();
         if (done) break;
         s.chunks++;
         buf += decoder.decode(value,{stream:true});
         const lines = buf.split('\n');
         buf = lines.pop();
         for (let line of lines) {
           if (!line.startsWith('data:')) continue;
           s.sseEvents++;
           let t = line.slice(5).trim();
           if (t === '[DONE]') { s.sseCompleted = true; continue; }
           try {
             let e = JSON.parse(t);
             let kind = String(e.type || e.o || 'unknown');
             s.markers[kind] = (s.markers[kind] || 0) + 1;
             if (e.type === 'message_stream_complete') s.sseCompleted = true;
           } catch (_) { /* SSE line may be non-JSON. */ }
         }
       }
     } catch(e) {
       s.streamError = e.name;
     } finally {
       s.sseFinished = true;
     }
   })();
   return response;
 };
})()"""

BACKEND_JS = r"""async (cid) => {
 try {
   let auth = await fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json());
   if (!auth.accessToken) return {error:'session_token_unavailable'};
   let r = await fetch('/backend-api/conversation/'+encodeURIComponent(cid),{
      credentials:'include',headers:{authorization:'Bearer '+auth.accessToken}
   });
   if (!r.ok) return {error:'history_http_'+r.status,
      retry_after:r.status===429?r.headers.get('retry-after'):null};
   let d=await r.json(), mapping=d.mapping||{}, id=d.current_node, chain=[],max=20000,
       newerMessage=null;
   while (id && max-- > 0) {
     let node=mapping[id], message=node?.message;
     if (!node) break;
     if (message) {
       // Some mapping nodes have no message. The next message ancestor is
       // derived from the ACTUAL mapping parent walk, not adjacent IDs.
       if(newerMessage)newerMessage.branch_parent_id=message.id||id;
       let entry={id:message.id||id,role:message.author?.role||'',
         status:message.status,end_turn:message.end_turn===true,
         parent_id:node.parent,create_time:message.create_time,
         content:(message.content?.parts||[]).filter(x=>typeof x==='string').join('\n'),
         recipient:message.recipient||'',
         images:(message.content?.parts||[]).filter(x=>x&&typeof x==='object'&&
            (x.asset_pointer||/asset_pointer/i.test(x.content_type||''))).map(x=>({
            content_type:x.content_type||'image',asset_pointer:x.asset_pointer||null,
            width:x.width||null,height:x.height||null,
            url:(typeof x.url==='string'&&x.url.length<1500)?x.url:null,
            mime_type:x.mime_type||null, name:x.name||null,
            size_bytes:x.size_bytes||null
         })),
         attachments:(message.metadata?.attachments||[]).filter(x=>x.asset_pointer||x.id).map(x=>({
            content_type:x.mime_type||x.content_type||'attachment',
            asset_pointer:x.asset_pointer||(String(x.id||'').startsWith('file_')?'sediment://'+x.id:null),
            name:x.name||null, mime_type:x.mime_type||null,
            size_bytes:x.size_bytes||null
         }))};
       chain.push(entry);
       newerMessage=entry;
     }
     id=node.parent;
   }
   // Strict lineage plus corroborating project identity metadata.
   return {conversation_id:d.conversation_id||cid,
      provider_conversation_id:typeof d.conversation_id==='string'?d.conversation_id:null,
      project_id:typeof d.project_id==='string'?d.project_id:null,
      chain:chain,chain_complete:!id,current_node:d.current_node||null};
 } catch(e) { return {error:e.name}; }
}"""

CHAT_DELETE_JS = r"""async ({cid,mutate}) => {
 const session=await fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json());
 if(!session?.accessToken)return {error:'not_authenticated'};
 const endpoint='/backend-api/conversation/'+encodeURIComponent(cid);
 const headers={authorization:'Bearer '+session.accessToken};
 const inspect=async()=>{
   const r=await fetch(endpoint,{credentials:'include',headers,cache:'no-store'});
   if(r.status===404||r.status===410)return {gone:true,status:r.status};
   if(!r.ok)return {error:'read_http_'+r.status,status:r.status};
   const d=await r.json();
   if((d.conversation_id||cid)!==cid)return {error:'wrong_conversation'};
   return {gone:d.is_visible===false,visible:d.is_visible!==false,status:r.status};
 };
 let before=await inspect();
 if(before.error||before.gone||!mutate)return {before,after:before,attempted:false};
 // ChatGPT web uses PATCH is_visible:false for the Delete chat action.
 // This is a private web API; do not confuse with api.openai.com/v1/conversations.
 let response;
 try {
   response=await fetch(endpoint,{method:'PATCH',credentials:'include',
     headers:{...headers,'content-type':'application/json'},
     body:JSON.stringify({is_visible:false})});
 }catch(e){return {before,attempted:true,error:'patch_exception_'+e.name}};
 const patch_status=response.status;
 if(!response.ok)return {before,attempted:true,patch_status,error:'patch_http_'+patch_status};
 const after=await inspect();
 return {before,after,attempted:true,patch_status};
}"""


DOWNLOAD_ASSET_JS = r"""async ({cid,pointer}) => {
 try {
   if (!/^(?:(?:sediment|file-service):\/\/)?file_[a-zA-Z0-9_\-]+$/.test(pointer))
     return {error:'unsupported_file_pointer'};
   const fid=pointer.includes('://')?pointer.split('://')[1]:pointer;
   const auth=await fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json());
   if(!auth.accessToken)return {error:'not_authenticated'};
   const r=await fetch('/backend-api/files/download/'+encodeURIComponent(fid)+
      '?conversation_id='+encodeURIComponent(cid)+'&inline=false',{
      credentials:'include',headers:{authorization:'Bearer '+auth.accessToken}
   });
   if(!r.ok)return {error:'resolve_http_'+r.status};
   const meta=await r.json();
   if(!meta.download_url)return {error:'no_download_url'};
   const url=new URL(meta.download_url,location.origin);
   if(url.protocol!=='https:' || !(url.hostname==='chatgpt.com' ||
      url.hostname.endsWith('.oaiusercontent.com')))
     return {error:'unexpected_download_origin'};
   const response=await fetch(url.href,{credentials:'include'});
   if(!response.ok)return {error:'download_http_'+response.status};
   if(Number(response.headers.get('content-length')||0)>25*1024*1024)
     return {error:'asset_too_large'};
   const bytes=new Uint8Array(await response.arrayBuffer());
   if(bytes.length<1||bytes.length>25*1024*1024)return {error:'asset_size_invalid'};
   let binary='';
   for(let i=0;i<bytes.length;i+=8192)
     binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
   return {data_base64:btoa(binary),
      mime_type:(response.headers.get('content-type')||'').split(';')[0],
      filename:meta.file_name||fid,width:meta.width||null,height:meta.height||null};
 }catch(e){return {error:'image_download_exception_'+e.name}}
}"""

CAPTURE_IMAGES_JS = r"""async () => {
 const main=document.querySelector('main')||document;
 const heading=[...main.querySelectorAll('h4[data-conversation-role="assistant"]')].at(-1);
 const container=heading?.closest('[data-message-author-role="assistant"]') || heading?.closest('article');
 if(!container)return [];
 const imgs=[...container.querySelectorAll('img')].filter(img =>
   img.naturalWidth>=64 && img.naturalHeight>=64 &&
   !img.closest('[data-user-message-bubble]') &&
   (/^(data:image\/|blob:|https:\/\/)/).test(img.currentSrc||img.src||'')
 );
 let result=[];
 for(const img of imgs.slice(-8)) {
   const src=img.currentSrc||img.src;
   try {
     const response=await fetch(src,{credentials:'include'});
     if(!response.ok) continue;
     const blob=await response.blob();
     if(blob.size>10*1024*1024 || blob.size<1 || !blob.type.startsWith('image/')) continue;
     const buffer=new Uint8Array(await blob.arrayBuffer());
     let encoded='';
     for(let i=0;i<buffer.length;i+=8192){
       encoded+=String.fromCharCode(...buffer.subarray(i,i+8192));
     }
     result.push({data_base64:btoa(encoded),mime_type:blob.type,
       filename:img.alt||'chatgpt-image',width:img.naturalWidth,height:img.naturalHeight});
   } catch(e) { /* Some remote images disallow same-origin fetch. */ }
 }
 return result;
}"""

DOM_JS = r"""() => {
 let headings=[...document.querySelectorAll('h4[data-conversation-role="assistant"]')];
 let h=headings.at(-1);
 let parent=h?.parentElement;
 let markdown=parent?.querySelector('.markdown');
 let content=(markdown?.innerText || parent?.innerText || '').trim();
 content=content.replace(/^(ChatGPT said:|ChatGPT сказал:)\s*/i,'');
 let state=window.__chatbridge||{};
 // The transport never retries a send when the ChatGPT UI reports a
 // network failure. Count only dedicated error nodes, not quoted user text.
 const main=document.querySelector('main');
 const errors=[...(main?.querySelectorAll('div')||[])].filter(el=>
   !el.childElementCount &&
   /^(Failed to fetch|Network error|Ошибка сети|Ошибка загрузки)$/i.test((el.innerText||'').trim()) &&
   !el.closest('[data-user-message-bubble],[data-message-author-role="user"]'));
 return {
   uiErrorCount:errors.length,
   assistantCount:headings.length,
   text:content.slice(0,500000),
   url:location.href, requestId:state.requestId, parentId:state.parentId,
   httpStatus:state.responseStatus,
   sseCompleted:state.sseCompleted,sseFinished:state.sseFinished,
   streamError:state.streamError,sseEvents:state.sseEvents,
   isGenerating:!!document.querySelector('button[data-testid="stop-button"]')
 };
}"""


def _turn_segment(
    chain: list[dict], expected_user_id: str | None, expected_parent_id: str | None = None
) -> list[dict] | None:
    """Selected provider branch, bounded by the EXACT user message ID.

    The backend traverses mapping[current_node].parent. Validate explicit
    adjacency as well; repeated prompt text cannot establish ownership.
    """
    if not expected_user_id:
        return None
    target_idx = next(
        (
            i
            for i, node in enumerate(chain)
            if node.get("role") == "user" and node.get("id") == expected_user_id
        ),
        None,
    )
    if target_idx is None:
        return None
    target = chain[target_idx]
    if expected_parent_id is not None and target.get("parent_id") != expected_parent_id:
        return None
    segment = chain[:target_idx]
    if any(node.get("role") == "user" for node in segment):
        return None
    for newer, older in zip(chain[:target_idx], chain[1 : target_idx + 1]):
        # The provider mapping can contain empty nodes between messages.
        # branch_parent_id is the next message actually traversed, whereas
        # parent_id is the raw immediate node.parent used for user correlation.
        parent = newer.get("branch_parent_id", newer.get("parent_id"))
        if ("branch_parent_id" in newer or "parent_id" in newer) and parent != older.get("id"):
            return None
    return segment


def _match_turn(
    chain: list[dict],
    expected_user_id: str | None,
    prompt: str,
    expected_parent_id: str | None = None,
):
    """Only a provider-terminal assistant on the selected user lineage."""
    segment = _turn_segment(chain, expected_user_id, expected_parent_id)
    if segment is None:
        return None
    for node in segment:
        if node.get("role") == "assistant":
            # An earlier final is not authoritative if a newer assistant
            # on this selected branch has begun but is still generating.
            return (
                node
                if (node.get("end_turn") is True and node.get("status") == "finished_successfully")
                else None
            )
    return None


def _turn_image_refs(
    chain: list[dict],
    expected_user_id: str | None,
    prompt: str,
    expected_parent_id: str | None = None,
) -> list[dict]:
    """Assets from assistant/tool nodes strictly inside this user turn."""
    segment = _turn_segment(chain, expected_user_id, expected_parent_id)
    if segment is None:
        return []
    refs: list[dict] = []
    seen: set[str] = set()
    for node in segment:
        if node.get("role") not in ("assistant", "tool"):
            continue
        for item in node.get("images", []) + node.get("attachments", []):
            key = item.get("asset_pointer") or item.get("url") or item.get("id")
            if key and key not in seen:
                seen.add(key)
                refs.append(item)
    return refs


class BrowserTransport:
    def __init__(
        self,
        cdp_url: str,
        hard_timeout_seconds: float = 1800,
        verify_interval_seconds: float = 1.5,
        reasoning_effort: str = "high",
        visible_background: bool = False,
    ):
        self.cdp_url = cdp_url
        self.hard_timeout = hard_timeout_seconds
        self.interval = verify_interval_seconds
        self.reasoning_effort = reasoning_effort
        self.visible_background = visible_background
        self._pw = None
        self._browser = None
        self._connect_lock = asyncio.Lock()
        self._marker_search_lock = asyncio.Lock()
        self._delete_lock = asyncio.Lock()
        self._owned_pages = set()
        self._rate_limit_handler = None
        self._rate_limit_checker = lambda: 0
        self._context_estimator = lambda sid: {"characters": 0, "messages": 0}
        self._history_reads = 0
        self._throttle_events = 0

    def set_context_estimator(self, estimator):
        self._context_estimator = estimator

    def set_rate_limit_handler(self, handler):
        self._rate_limit_handler = handler

    def set_rate_limit_checker(self, checker):
        self._rate_limit_checker = checker

    def _observe_provider_response(self, response):
        # HTTP 429 on ChatGPT's own origin can occur during initial page
        # navigation as well as from the conversation endpoints.
        if response.status != 429 or not response.url.startswith("https://chatgpt.com/"):
            return
        self._throttle_events += 1
        if self._rate_limit_handler:
            self._rate_limit_handler(
                retry_after_seconds(response.headers.get("retry-after")),
                "provider_http_429",
            )

    async def _ensure(self):
        # An authenticated Chrome instance may restart independently of the
        # HTTP daemon. Check the CDP transport before reusing a cached browser.
        if self._browser and self._browser.is_connected():
            return self._browser
        async with self._connect_lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            self._browser = None
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    log.debug("Error disposing disconnected Playwright driver", exc_info=True)
                self._pw = None
            self._pw = await async_playwright().start()
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(
                    self.cdp_url, timeout=15000
                )
            except Exception:
                await self._pw.stop()
                self._pw = None
                raise
        return self._browser

    async def _new_page(self, browser):
        """Create a visible tab without activating the Chrome app on macOS.

        Playwright context.new_page() issues Target.createTarget without
        background/focus and can steal the operator's active window.
        We deliberately do not fall back to that method in visible mode.
        """
        context = browser.contexts[0]
        if not self.visible_background:
            return await context.new_page()
        session = await browser.new_browser_cdp_session()
        try:
            async with context.expect_page(timeout=15000) as event:
                await session.send(
                    "Target.createTarget",
                    {
                        "url": "about:blank",
                        "background": True,
                        "focus": False,
                    },
                )
            return await event.value
        finally:
            await session.detach()

    async def _configure_reasoning_effort(self, page, effort: str) -> None:
        """Select native ChatGPT reasoning slider and verify its actual state."""
        levels = {"low": 0, "medium": 1, "high": 2}
        if effort not in levels:
            raise ValueError("Unsupported reasoning effort")
        # The ChatGPT SPA may keep its bare HTML shell on screen for 20+ sec
        # after DOMContentLoaded. Wait for the real editor before inspecting the
        # model picker; the old 17s picker timeout mistook hydration lag for failure.
        editor = page.locator('div[contenteditable="true"].ProseMirror').first
        try:
            try:
                await editor.wait_for(state="visible", timeout=32000)
            except PlaywrightTimeoutError:
                # Only retry navigation before Send, never an uncertain submit.
                if self._rate_limit_checker() > 0:
                    raise ProviderRateLimitError("Provider cooldown while loading ChatGPT composer")
                log.warning("ChatGPT editor unavailable after initial navigation; reloading one time")
                await page.reload(wait_until="domcontentloaded", timeout=40000)
                await editor.wait_for(state="visible", timeout=42000)
            picker = page.locator('button[data-composer-navigation-target="reasoning"]').first
            if await picker.count() == 0:
                picker = page.get_by_role(
                    "button", name=re.compile(r"(?:Select ChatGPT model|Выбрать модель ChatGPT)", re.I)
                ).first
            await picker.wait_for(state="visible", timeout=20000)
            # CDP tabs are deliberately opened in the background. A Playwright
            # pointer click may hang waiting for focus/navigation even when the
            # React control responds. Native DOM click works without stealing
            # the foreground window and is verified by the slider below.
            await picker.evaluate("(el) => el.click()")
        except Exception as exc:
            raise RuntimeError("ChatGPT reasoning/model picker unavailable") from exc
        try:
            control = page.locator("[data-reasoning-slider]").first
            await control.wait_for(state="visible", timeout=6000)
            value = control.locator("[aria-valuenow]").first
            actual = int(await value.get_attribute("aria-valuenow") or -1)
            target = levels[effort]
            if actual != target:
                await control.focus()
                for _ in range(abs(target - actual)):
                    await control.press("ArrowRight" if target > actual else "ArrowLeft")
                    await asyncio.sleep(0.08)
            verified = int(await value.get_attribute("aria-valuenow") or -1)
            if verified != target:
                raise RuntimeError(f"ChatGPT reasoning effort did not reach {effort}")
        finally:
            await page.keyboard.press("Escape")

    @staticmethod
    async def _fill_composer(page, editor, prompt: str) -> None:
        """Insert and verify text before pressing Send, even in background tabs.

        A Playwright locator.fill sometimes times out on an otherwise usable
        ProseMirror editor. Keyboard insertion is safe here because no Send has
        been attempted. Never retry Send itself.
        """
        try:
            await editor.fill(prompt, timeout=18000)
        except PlaywrightTimeoutError:
            log.warning("ChatGPT ProseMirror fill timed out; trying keyboard insert before Send")
            await editor.wait_for(state="visible", timeout=11000)
            await editor.evaluate("""el => {
                el.focus();
                const range = document.createRange();
                range.selectNodeContents(el);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
            }""")
            await page.keyboard.insert_text(prompt)
        rendered = await editor.evaluate("(el) => el.innerText || el.textContent || ''")
        expected_norm = re.sub(r"\s+", " ", prompt).strip()
        actual_norm = re.sub(r"\s+", " ", rendered).strip()
        if (not expected_norm or
                not actual_norm.startswith(expected_norm[:min(80,len(expected_norm))]) or
                len(actual_norm) < 0.93 * len(expected_norm)):
            raise RuntimeError("ChatGPT editor did not retain the full message; no Send attempted")

    async def inspect_conversation(self, conversation_id: str, *, project_id: str) -> dict:
        """Verify a remote conversation loads under the claimed project URL."""
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", conversation_id, re.I):
            raise ValueError("Invalid conversation ID")
        if not re.fullmatch(r"g-p-[0-9a-f]{32}", project_id):
            raise ValueError("Invalid project ID")
        if self._rate_limit_checker() > 0:
            raise ProviderRateLimitError("Provider cooldown prevents chat import verification")
        browser = await self._ensure()
        page = await self._new_page(browser)
        self._owned_pages.add(page)
        try:
            page.on("response", self._observe_provider_response)
            await page.goto(
                f"https://chatgpt.com/g/{project_id}/c/{conversation_id}",
                wait_until="domcontentloaded",
                timeout=35000,
            )
            loaded = page.locator(
                'h4[data-conversation-role="assistant"],'
                "[data-user-message-bubble],"
                '[data-message-author-role="user"]'
            ).first
            await loaded.wait_for(state="visible", timeout=23000)
            if not re.fullmatch(
                rf"https://chatgpt[.]com/g/{re.escape(project_id)}(?:-[a-z0-9-]+)?/c/{re.escape(conversation_id)}",
                page.url,
                re.I,
            ):
                raise RuntimeError("Conversation redirected outside requested project")
            self._history_reads += 1
            history = await page.evaluate(BACKEND_JS, conversation_id)
            return verify_conversation_membership(history, conversation_id, project_id)
        finally:
            self._owned_pages.discard(page)
            if not page.is_closed():
                await page.close()

    async def inspect_project(self, project_id: str) -> dict:
        """Read real project name/memory from ChatGPT settings, without editing."""
        if not re.fullmatch(r"g-p-[0-9a-f]{32}", project_id):
            raise ValueError("Invalid ChatGPT project ID")
        if self._rate_limit_checker() > 0:
            raise ProviderRateLimitError("Provider cooldown prevents project verification")
        browser = await self._ensure()
        if not browser.contexts:
            raise RuntimeError("No authenticated Chrome context")
        page = await self._new_page(browser)
        self._owned_pages.add(page)
        try:
            page.on("response", self._observe_provider_response)
            await page.goto(
                f"https://chatgpt.com/g/{project_id}/project",
                wait_until="commit",
                timeout=30000,
            )
            button = page.get_by_role(
                "button", name=re.compile(r"Действия с проектом|Project actions", re.I)
            )
            await button.first.wait_for(state="visible", timeout=23000)
            if f"/g/{project_id}/" not in page.url:
                raise RuntimeError("Project URL redirected to another project")
            await button.first.click()
            settings = page.get_by_text(re.compile(r"^Настройки проекта$|^Project settings$", re.I))
            await settings.last.click()
            dialog = page.get_by_role("dialog")
            await dialog.first.wait_for(state="visible", timeout=10000)
            name = await dialog.locator(
                'input[aria-label="Название проекта"],input[aria-label="Project name"]'
            ).first.input_value()
            sections = dialog.locator("section")
            memory_label = None
            for i in range(await sections.count()):
                section = sections.nth(i)
                heading = (await section.locator("span").first.inner_text()).strip()
                if heading in ("Память", "Memory"):
                    memory_label = (await section.locator("button").first.inner_text()).strip()
                    break
            if memory_label is None:
                raise RuntimeError("Project memory configuration not visible")
            if re.search(r"^По умолчанию$|^Default(?: memory)?$", memory_label, re.I):
                memory_mode = "default"
            elif re.search(
                r"Память только в проекте|Только в проекте|Project.only", memory_label, re.I
            ):
                memory_mode = "project_only"
            else:
                raise RuntimeError("Unrecognized project memory mode")
            return {"project_id": project_id, "name": name, "memory_mode": memory_mode}
        finally:
            self._owned_pages.discard(page)
            if not page.is_closed():
                await page.close()

    async def create_project(self, name: str, memory_mode: str) -> str:
        """Create a ChatGPT project via the visible UI; return its stable ID.

        The gateway never infers an app-to-project mapping. Project creation
        is performed only on an explicit API request from a client.
        """
        if not (1 <= len(name.strip()) <= 120):
            raise ValueError("Project name must contain 1..120 characters")
        if memory_mode not in ("default", "project_only"):
            raise ValueError("Invalid project memory mode")
        browser = await self._ensure()
        if not browser.contexts:
            raise RuntimeError("No authenticated Chrome context")
        page = await self._new_page(browser)
        self._owned_pages.add(page)
        try:
            page.on("response", self._observe_provider_response)
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=40000)
            if self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Provider limited project creation before user action")
            new_project = page.get_by_role(
                "button", name=re.compile(r"Добавить новый проект|Add new project", re.I)
            )
            await new_project.first.wait_for(state="visible", timeout=25000)
            # Sidebar panel divider may overlay the icon. Trigger the element's
            # native DOM click instead of a coordinate click through that divider.
            await new_project.first.evaluate("el => el.click()")
            dialog = page.get_by_role("dialog")
            await dialog.locator('input[type="text"]').first.fill(name.strip())
            # Validate the chosen memory mode in the UI before the irreversible
            # Create click; never assume a label that the platform did not show.
            current_memory = await dialog.inner_text()
            if memory_mode == "default" and not re.search(
                r"Память по умолчанию|Default memory", current_memory, re.I
            ):
                raise RuntimeError("Default project memory selection was not verified")
            desired = "Память только в проекте" if memory_mode == "project_only" else None
            if memory_mode == "project_only":
                switch = dialog.get_by_role(
                    "button", name=re.compile(r"Память по умолчанию|Default memory", re.I)
                )
                await switch.click()
                option = page.get_by_text(
                    re.compile(r"^Память только в проекте$|^Project-only memory$", re.I)
                )
                await option.first.click()
                label = await dialog.inner_text()
                if not (desired in label or "Project-only memory" in label):
                    raise RuntimeError("Project-only memory was not selected")
            create = dialog.get_by_role(
                "button", name=re.compile(r"^Создать проект$|^Create project$", re.I)
            )
            if self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Provider cooldown before Create project click")
            await create.click()
            await page.wait_for_url(re.compile(r"/g/g-p-[0-9a-f]{32}"), timeout=35000)
            match = re.search(r"/g/(g-p-[0-9a-f]{32})", page.url)
            if not match:
                raise RuntimeError("Project created but no stable ID was found")
            return match.group(1)
        finally:
            self._owned_pages.discard(page)
            if not page.is_closed():
                await page.close()

    async def generate(
        self,
        *,
        session_id: str,
        conversation_id: str | None,
        prompt: str,
        request_id: str,
        images: list[ImagePayload] | None = None,
        reasoning_effort: str | None = None,
        project_id: str | None = None,
        temporary: bool = False,
    ) -> AsyncIterator[TransportEvent]:
        if temporary and (project_id or conversation_id):
            raise ValueError("Temporary chat cannot continue or belong to a project")
        if project_id and not re.fullmatch(r"g-p-[0-9a-f]{32}", project_id):
            raise ValueError("Invalid project ID")
        browser = await self._ensure()
        if not browser.contexts:
            raise RuntimeError("No authenticated browser context")
        page = await self._new_page(browser)
        self._owned_pages.add(page)
        submitted_attempted = False
        emitted = ""
        start = time.monotonic()
        # A DOM probe is local. A backend history probe costs authenticated
        # provider traffic (auth/session + conversation) and is expensive in
        # bulk; keep it well below one poll per two seconds per active turn.
        context_hint = self._context_estimator(session_id)
        initial_probe_delay = first_probe_delay(
            len(prompt),
            context_hint.get("characters", 0),
            context_hint.get("messages", 0),
            len(images or ()),
            reasoning_effort or self.reasoning_effort,
        )
        verification = None

        approval_key = None
        navigation_limit = {"retry_until": 0.0, "retry_after": 0.0}

        def observe_conversation_throttle(response):
            # ChatGPT's UI loads existing chats with the plural /conversations/
            # route, separate from the singular /conversation/ history endpoint.
            # A 429 here prevents the original composer from loading.
            if not conversation_id or response.status != 429:
                return
            if not re.search(
                rf"/backend-api/conversations?/{re.escape(conversation_id)}(?:\?|$)",
                response.url,
                re.I,
            ):
                return
            raw = response.headers.get("retry-after", "")
            seconds = float(raw) if re.fullmatch(r"\d+(?:\.\d+)?", raw) else 30.0
            navigation_limit["retry_after"] = seconds
            navigation_limit["retry_until"] = time.monotonic() + seconds

        try:
            page.on("response", self._observe_provider_response)
            page.on("response", observe_conversation_throttle)
            await page.add_init_script(INTERCEPT_JS)
            if temporary:
                target = "https://chatgpt.com/?temporary-chat=true"
            elif project_id:
                target = (
                    f"https://chatgpt.com/g/{project_id}/c/{conversation_id}"
                    if conversation_id
                    else f"https://chatgpt.com/g/{project_id}/project"
                )
            else:
                target = (
                    f"https://chatgpt.com/c/{conversation_id}"
                    if conversation_id
                    else "https://chatgpt.com/"
                )
            await page.goto(target, wait_until="domcontentloaded", timeout=40000)
            if temporary:
                temporary_toggle = page.get_by_role(
                    "button",
                    name=re.compile(r"Выйти из режима временного чата|Exit temporary", re.I),
                )
                await temporary_toggle.first.wait_for(state="visible", timeout=25000)
            if project_id and f"/g/{project_id}" not in page.url:
                raise RuntimeError("ChatGPT navigation lost the requested project")
            if conversation_id:
                history_visible = page.locator('h4[data-conversation-role="assistant"]').first
                try:
                    await history_visible.wait_for(state="visible", timeout=27000)
                except PlaywrightTimeoutError as exc:
                    if navigation_limit["retry_after"] or self._rate_limit_checker():
                        raise ProviderRateLimitError(
                            "ChatGPT conversation loading rate limited before Send"
                        ) from exc
                    raise RuntimeError(
                        "ChatGPT conversation could not load; no message sent"
                    ) from exc
            if self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Provider cooldown before entering a message")
            # Check the usable composer *after* any provider-directed load retry.
            # A 429 page has no editor; checking it first would bypass the retry.

            await self._configure_reasoning_effort(page, reasoning_effort or self.reasoning_effort)
            editor = page.locator('div[contenteditable="true"].ProseMirror').first
            await editor.wait_for(state="visible", timeout=30000)
            baseline_state = await page.evaluate(DOM_JS)
            baseline = baseline_state.get("assistantCount", 0)
            baseline_ui_errors = baseline_state.get("uiErrorCount", 0)
            # Imported chats can have more actual history than the gateway's
            # local SQLite copy. The loaded DOM is a no-network size hint.
            if baseline:
                initial_probe_delay = first_probe_delay(
                    len(prompt),
                    max(context_hint.get("characters", 0), baseline * 1000),
                    max(context_hint.get("messages", 0), baseline * 2),
                    len(images or ()),
                    reasoning_effort or self.reasoning_effort,
                )
            if images:
                uploaded = []
                for i, image in enumerate(images):
                    uploaded.append(
                        {
                            "name": f"cb-{uuid.uuid4().hex[:16]}-{i}-{image.name}",
                            "mimeType": image.mime_type,
                            "buffer": image.data,
                        }
                    )
                file_input = page.locator('input[type=file][aria-label="Прикрепить файлы"]').first
                if not await file_input.count():
                    file_input = page.locator("input[type=file]:not([capture])").last
                await file_input.set_input_files(uploaded)
                # File chips have a progressbar while ChatGPT uploads the
                # attachment. An image also has an intermediate blob: preview.
                # Do not submit until each matching chip exists and finishes.
                for file in uploaded:
                    for attempt in range(120):
                        state = await page.evaluate(
                            """filename => {
                            const host=document.querySelector('[data-composer-attachments]');
                            const chips=[...(host?.querySelectorAll(':scope > div > *')||[])];
                            const chip=chips.find(el=>el.innerText.includes(filename)||
                                [...el.querySelectorAll('img')].some(i=>i.alt===filename));
                            if(!chip)return {found:false,done:false};
                            const progress=!!chip.querySelector('[role=progressbar]');
                            const img=chip.querySelector('img');
                            const previewReady=!img || (img.currentSrc||img.src||'').startsWith('data:image/');
                            return {found:true,done:!progress&&previewReady};
                        }""",
                            file["name"],
                        )
                        if state.get("found") and state.get("done"):
                            break
                        await asyncio.sleep(0.5)
                    else:
                        raise RuntimeError("ChatGPT file attachment did not become ready")
            if self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Shared provider cooldown before submission")
            await self._fill_composer(page, editor, prompt)
            if self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Shared provider cooldown before pressing Send")
            if conversation_id:
                submitted_attempted = True
                button = page.get_by_role("button", name=re.compile(r"^(Send|Отправить)$", re.I))
                if await button.count() and await button.first.is_enabled():
                    await button.first.click()
                else:
                    await editor.press("Enter")
            else:
                submitted_attempted = True
                await editor.press("Enter")
            # The first click/Enter may still be in flight without DOM or fetch
            # acknowledgement. A second click is NOT proof of non-delivery and
            # risks a duplicate model invocation: preserve unknown instead.
            await asyncio.sleep(1.3)
            initial_state = await page.evaluate(DOM_JS)
            if not initial_state.get("requestId"):
                raise SubmissionUncertainError(
                    "No provider user-message ID after Send; never click Send again"
                )
            # We cannot certify submission from a click alone.
            first_submitted = False
            initial_conversation = conversation_id
            announced_conversation = conversation_id
            while time.monotonic() - start < self.hard_timeout:
                if page.is_closed():
                    raise RuntimeError("Active browser tab closed unexpectedly")
                try:
                    dom = await page.evaluate(DOM_JS)
                except Exception:
                    await asyncio.sleep(1)
                    continue
                url = dom.get("url", "")
                cid_match = re.search(r"/c/([0-9a-f-]{36})", url)
                cid = cid_match.group(1) if cid_match else conversation_id
                user_id = dom.get("requestId")
                if (
                    submitted_attempted
                    and dom.get("uiErrorCount", 0) > baseline_ui_errors
                    and not dom.get("sseCompleted")
                ):
                    raise SubmissionUncertainError(
                        "ChatGPT displayed a network failure after Send; "
                        "the prompt must not be retransmitted automatically"
                    )
                if not first_submitted and user_id:
                    if cid and not temporary:
                        announced_conversation = cid
                    yield TransportEvent(
                        "submitted",
                        conversation_id=cid,
                        message_id=user_id,
                        parent_message_id=dom.get("parentId"),
                    )
                    first_submitted = True
                    verification = VerificationSchedule(time.monotonic(), initial_probe_delay)
                    yield TransportEvent("thinking")
                elif not first_submitted and cid and not initial_conversation:
                    # Retain ambiguity until message ID can be correlated.
                    pass
                if first_submitted and not temporary and cid and cid != announced_conversation:
                    # The first submitted event can arrive before ChatGPT
                    # assigns a new project's /c/{id} URL. Persist that ID
                    # independently so HTTP-429 recovery remains possible.
                    announced_conversation = cid
                    yield TransportEvent("conversation_bound", conversation_id=cid)

                approval = await self._approve_if_needed(page)
                if approval:
                    key = (
                        approval["tool"],
                        approval["operation"],
                        approval["scope"],
                        approval["key"],
                    )
                    if key != approval_key:
                        approval_key = key
                        yield TransportEvent(
                            "awaiting_approval",
                            tool=approval["tool"],
                            operation=approval["operation"],
                        )
                        yield TransportEvent(
                            "approval_applied",
                            tool=approval["tool"],
                            operation=approval["operation"],
                            scope=approval["scope"],
                            meta={"result": approval["result"]},
                        )
                    await asyncio.sleep(0.25)
                    continue

                dom_text = dom.get("text", "") if dom.get("assistantCount", 0) > baseline else ""
                if first_submitted and dom_text and dom_text != emitted:
                    if dom_text.startswith(emitted):
                        yield TransportEvent("text_delta", content=dom_text[len(emitted) :])
                    else:
                        yield TransportEvent("text_replace", content=dom_text)
                    emitted = dom_text

                now = time.monotonic()
                # DOM/SSE are already local to the open Chrome tab; unlike
                # BACKEND_JS, inspecting them adds no authenticated history read.
                likely_finished = bool(
                    dom.get("sseCompleted")
                    or (dom.get("sseFinished") and not dom.get("isGenerating"))
                    or (
                        dom_text
                        and not dom.get("isGenerating")
                        and now - verification.started_at >= 3.0
                        if verification
                        else False
                    )
                )
                should_verify = bool(
                    verification
                    and cid
                    and first_submitted
                    and not self._rate_limit_checker()
                    and verification.should_check(
                        now,
                        likely_finished=likely_finished,
                        actively_generating=bool(dom.get("isGenerating")),
                    )
                )
                if should_verify:
                    self._history_reads += 1
                    history = await page.evaluate(BACKEND_JS, cid)
                    is_throttled = history.get("error") == "history_http_429"
                    verification.checked(
                        now,
                        rate_limited=is_throttled,
                        retry_after=max(
                            self._rate_limit_checker(),
                            retry_after_seconds(history.get("retry_after")),
                        )
                        if is_throttled
                        else 0,
                    )
                    if not history.get("error") and history.get("chain_complete") is not False:
                        final = _match_turn(history.get("chain", []), user_id, prompt)
                        if final:
                            refs = _turn_image_refs(history.get("chain", []), user_id, prompt)
                            if refs:
                                yield TransportEvent("artifact_refs", meta={"refs": refs})
                            seen_pointers = set()
                            pointer_binary_hashes = set()
                            for ref in refs:
                                pointer = ref.get("asset_pointer") or ""
                                if not pointer or pointer in seen_pointers:
                                    continue
                                seen_pointers.add(pointer)
                                resolved = await page.evaluate(
                                    DOWNLOAD_ASSET_JS, {"cid": cid, "pointer": pointer}
                                )
                                if resolved.get("data_base64"):
                                    raw = base64.b64decode(resolved["data_base64"], validate=True)
                                    pointer_binary_hashes.add(
                                        __import__("hashlib").sha256(raw).hexdigest()
                                    )
                                    kind = (
                                        "image"
                                        if (resolved.get("mime_type") or "").startswith("image/")
                                        else "artifact"
                                    )
                                    yield TransportEvent(kind, meta=resolved)
                                else:
                                    yield TransportEvent(
                                        "file_unavailable",
                                        meta={
                                            "asset_pointer": pointer,
                                            "reason": resolved.get("error", "not_downloadable"),
                                        },
                                    )
                            async for media, problem in download_visible_artifacts(
                                page, final.get("id"), final.get("content", "")
                            ):
                                if media is not None:
                                    if media.digest not in pointer_binary_hashes:
                                        yield TransportEvent(
                                            "image"
                                            if media.mime_type.startswith("image/")
                                            else "artifact",
                                            meta={
                                                "data_base64": base64.b64encode(
                                                    media.data
                                                ).decode(),
                                                "mime_type": media.mime_type,
                                                "filename": media.name,
                                            },
                                        )
                                else:
                                    yield TransportEvent("file_unavailable", meta=problem or {})
                            if not refs:
                                # Some image-only replies have no resource cards.
                                for item in await page.evaluate(CAPTURE_IMAGES_JS):
                                    yield TransportEvent("image", meta=item)
                            # The backend is definitive for completion; emitted DOM may omit
                            # whitespace/code blocks or include interim status labels.
                            final_text = final.get("content", "")
                            if final_text and final_text != emitted:
                                if final_text.startswith(emitted):
                                    yield TransportEvent(
                                        "text_delta", content=final_text[len(emitted) :]
                                    )
                                else:
                                    yield TransportEvent("text_replace", content=final_text)
                            yield TransportEvent(
                                "completed",
                                conversation_id=cid,
                                message_id=final.get("id"),
                                parent_message_id=user_id,
                            )
                            return
                if dom.get("httpStatus") == 429:
                    raise SubmissionUncertainError(
                        "Provider HTTP 429 after submission; do not resend"
                    )
                if dom.get("httpStatus") not in (None, 200):
                    raise RuntimeError(f"Browser request returned HTTP {dom['httpStatus']}")
                # No provider request is performed by this sleep. Long prompt
                # prefilling has no reason for a 2Hz browser DOM probe either.
                quiet = bool(verification and not emitted and not dom.get("sseCompleted"))
                await asyncio.sleep(2.0 if quiet else 0.7)
            raise TimeoutError("No confirmed end_turn within configured hard timeout")
        except Exception as exc:
            if submitted_attempted and not isinstance(exc, SubmissionUncertainError):
                raise SubmissionUncertainError(
                    "Turn send was attempted; cannot prove non-submission"
                ) from exc
            if not submitted_attempted and self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Provider limit detected before Send") from exc
            raise

        finally:
            self._owned_pages.discard(page)
            if not page.is_closed():
                await page.close()

    async def _approve_if_needed(self, page):
        """Auto-approve UI MCP requests per the user's explicit gateway policy.

        Never fabricate approval on a missing button. No message to the user.
        """
        try:
            # Current UI presents an inline approval card, not a dialog.
            forms = page.locator("form").filter(
                has=page.get_by_role(
                    "button", name=re.compile(r"Allow once|Разрешить один раз", re.I)
                )
            )
            for idx in range(await forms.count()):
                form = forms.nth(idx)
                if not await form.is_visible():
                    continue
                card = form.locator("xpath=..")
                raw = (await card.inner_text())[:2500]
                if not re.search(
                    r"(Разрешить .{0,40}(?:использовать|доступ)|Allow .{0,40}(?:to use|to access|to run)|MCP|Mac Control)",
                    raw,
                    re.I,
                ):
                    continue
                if re.search(r"Подозрительная инструкция|Suspicious instruction", raw, re.I):
                    # Policy: flagged requests are approved like any other.
                    log.warning(
                        "MCP approval flagged as suspicious by platform; approving per policy"
                    )
                tool_match = re.search(r"Mac Control|[\\w.-]+ MCP", raw, re.I)
                tool = tool_match.group(0) if tool_match else None
                op_match = re.search(r"(?:operation|операция)\\s*[:=]\\s*([\\w.-]+)", raw, re.I)
                operation = op_match.group(1) if op_match else None
                button = form.get_by_role(
                    "button",
                    name=re.compile(
                        r"for this conversation|на (?:весь|всю) (?:разговор|диалог|беседу)", re.I
                    ),
                )
                scope = None
                if await button.count() and await button.first.is_visible():
                    await button.first.click()
                    scope = "conversation"
                else:
                    # The right-hand side of the split "Allow once" control
                    # opens a menu with additional approval scopes. Probe
                    # that menu before falling back to one-time approval.
                    once = form.get_by_role(
                        "button", name=re.compile(r"Allow once|Разрешить один раз", re.I)
                    )
                    if await once.count() and await once.first.is_visible():
                        group = once.first.locator("xpath=..")
                        split_buttons = group.locator("button")
                        if await split_buttons.count() >= 2:
                            arrow = split_buttons.last
                            if await arrow.is_visible() and await arrow.is_enabled():
                                await arrow.click()
                                session_scope = re.compile(
                                    r"for (?:this|the) (?:conversation|chat|session)|"
                                    r"(?:весь|всю|этот|эту) (?:диалог|беседу|сессию|разговор)|"
                                    r"(?:в|для) (?:этого|текущего) (?:чата|диалога)|"
                                    r"до конца (?:беседы|разговора|сессии)",
                                    re.I,
                                )
                                deadline = time.monotonic() + 3.0
                                while not scope and time.monotonic() < deadline:
                                    for choice in (
                                        page.get_by_role("menuitem", name=session_scope),
                                        page.get_by_role("option", name=session_scope),
                                        page.get_by_role("button", name=session_scope),
                                    ):
                                        if await choice.count() and await choice.first.is_visible():
                                            await choice.first.click()
                                            scope = "conversation"
                                            break
                                    if not scope:
                                        await asyncio.sleep(0.15)
                                if not scope:
                                    try:
                                        items = await page.get_by_role("menuitem").all_inner_texts()
                                    except Exception:
                                        items = []
                                    log.warning(
                                        "MCP approval: no session-scope option in menu: %s", items
                                    )
                                if not scope:
                                    await page.keyboard.press("Escape")
                        if not scope and await once.first.is_visible():
                            await once.first.click()
                            scope = "once"
                if scope:
                    return {
                        "tool": tool,
                        "operation": operation,
                        "scope": scope,
                        "key": raw[:180],
                        "result": "clicked",
                    }
                log.warning("MCP approval card has no recognized scoped option; will retry")
                continue

            # Legacy dialog support.
            dialogs = page.get_by_role("dialog")
            count = await dialogs.count()
            for idx in range(count):
                dialog = dialogs.nth(idx)
                if not await dialog.is_visible():
                    continue
                raw = (await dialog.inner_text())[:1500]
                if not re.search(
                    r"(Разрешить.*(?:использовать|Mac Control)|Allow.*(?:use|access|MCP))",
                    raw,
                    re.I | re.S,
                ):
                    continue
                tool = (re.search(r"Mac Control|[\\w.-]+ MCP", raw, re.I) or [None])[0]
                op_match = re.search(r"(?:operation|операция)\\s*[:=]\\s*([\\w.-]+)", raw, re.I)
                operation = op_match.group(1) if op_match else None
                key = raw[:180]
                scope = None
                for locator in [
                    dialog.get_by_role(
                        "button", name=re.compile("conversation|весь разговор|всю беседу", re.I)
                    ),
                    dialog.get_by_role(
                        "menuitem", name=re.compile("conversation|весь разговор", re.I)
                    ),
                ]:
                    if await locator.count() and await locator.first.is_visible():
                        await locator.first.click()
                        scope = "conversation"
                        break
                if not scope:
                    option = dialog.locator(
                        'button[aria-label="Параметры утверждения"],button[aria-label*="approval"],button[aria-label*="options"]'
                    )
                    if await option.count() and await option.first.is_visible():
                        await option.first.click()
                        choice = page.get_by_role(
                            "menuitem",
                            name=re.compile(
                                r"for this conversation|весь (?:разговор|диалог)", re.I
                            ),
                        )
                        if await choice.count() and await choice.first.is_visible():
                            await choice.first.click()
                            scope = "conversation"
                if not scope:
                    choice = page.get_by_role(
                        "menuitem", name=re.compile("Allow once|Разрешить один раз", re.I)
                    )
                    if await choice.count() and await choice.first.is_visible():
                        await choice.first.click()
                        scope = "once"
                if not scope:
                    choice = dialog.get_by_role(
                        "button", name=re.compile("Allow once|Разрешить один раз", re.I)
                    )
                    if await choice.count() and await choice.first.is_visible():
                        await choice.first.click()
                        scope = "once"
                if scope:
                    return {
                        "tool": tool,
                        "operation": operation,
                        "scope": scope,
                        "key": key,
                        "result": "clicked",
                    }
                log.warning("MCP approval dialog has no enabling UI action; will retry")
                continue
        except RuntimeError:
            raise
        except Exception as e:
            log.warning("MCP UI approval inspection failed: %s", type(e).__name__)
        return None

    async def resolve_chat_marker(self, marker: str) -> dict:
        """Read-only built-in ChatGPT search, serialized with provider cooldown."""
        from .marker_lookup import resolve_in_browser, validate_marker

        validate_marker(marker)
        if self._rate_limit_checker() > 0:
            raise ProviderRateLimitError("Provider cooldown prevents marker lookup")
        async with self._marker_search_lock:
            if self._rate_limit_checker() > 0:
                raise ProviderRateLimitError("Provider cooldown prevents marker lookup")
            browser = await self._ensure()
            return await resolve_in_browser(
                browser, marker, self._observe_provider_response, new_page=self._new_page
            )

    async def read_conversation(
        self, conversation_id: str, *, project_id: str | None = None
    ) -> dict:
        if self._rate_limit_checker() > 0:
            raise ProviderRateLimitError("Provider cooldown prevents history polling")

        browser = await self._ensure()
        page = await self._new_page(browser)
        self._owned_pages.add(page)
        try:
            page.on("response", self._observe_provider_response)
            url = (
                f"https://chatgpt.com/g/{project_id}/c/{conversation_id}"
                if project_id
                else f"https://chatgpt.com/c/{conversation_id}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=35000)
            self._history_reads += 1

            history = await page.evaluate(BACKEND_JS, conversation_id)
            if "error" in history:
                raise RuntimeError("Conversation read failed: " + str(history["error"]))
            if history.get("chain_complete") is False:
                raise RuntimeError("Selected provider branch was not fully traversed")
            return history
        finally:
            self._owned_pages.discard(page)
            if not page.is_closed():
                await page.close()

    async def _conversation_deletion_check(self, conversation_id: str, *, mutate: bool) -> bool:
        """Inspect or delete *one* physical chat; never delete an entire project.

        A provider 2xx is insufficient: a subsequent authenticated GET must
        prove is_visible=false or an absent conversation. Any uncertain result
        remains unknown in the caller's durable operation journal.
        """
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", conversation_id, re.I):
            raise ValueError("Invalid conversation ID")
        if self._rate_limit_checker() > 0:
            raise ProviderRateLimitError("Provider cooldown prevents deletion")
        async with self._delete_lock:
            browser = await self._ensure()
            if not browser.contexts:
                raise RuntimeError("No authenticated browser context")
            page = await self._new_page(browser)
            self._owned_pages.add(page)
            try:
                page.on("response", self._observe_provider_response)
                # Navigate once per explicit operation. No provider model prompt.
                await page.goto(
                    "https://chatgpt.com/", wait_until="domcontentloaded", timeout=35000
                )
                outcome = await page.evaluate(
                    CHAT_DELETE_JS,
                    {
                        "cid": conversation_id,
                        "mutate": mutate,
                    },
                )
                if not isinstance(outcome, dict):
                    raise RuntimeError("Malformed provider delete response")
                before, after = outcome.get("before") or {}, outcome.get("after") or {}
                if before.get("error") or outcome.get("error") or after.get("error"):
                    raise RuntimeError(
                        "Provider deletion outcome not confirmed: "
                        + str(outcome.get("error") or after.get("error") or before.get("error"))[
                            :100
                        ]
                    )
                return after.get("gone") is True
            finally:
                self._owned_pages.discard(page)
                if not page.is_closed():
                    await page.close()

    async def delete_conversation(self, conversation_id: str) -> bool:
        return await self._conversation_deletion_check(conversation_id, mutate=True)

    async def verify_conversation_deleted(self, conversation_id: str) -> bool:
        return await self._conversation_deletion_check(conversation_id, mutate=False)

    async def close(self):
        for page in tuple(self._owned_pages):
            try:
                await page.close()
            except Exception:
                pass
        self._owned_pages.clear()
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
