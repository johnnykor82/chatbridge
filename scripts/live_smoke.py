"""Live CDP regression test. Creates only temporary owned tabs in existing test profile."""
import asyncio, json, time
from chatbridge.browser_transport import BrowserTransport


async def go():
    transport=BrowserTransport("http://127.0.0.1:9335",
                               hard_timeout_seconds=115,verify_interval_seconds=2.2)
    try:
        async def run(label,conversation_id=None):
            started=time.monotonic()
            parts=[];final=None;cid=conversation_id
            prompt=("Browser gateway independent test "+label+
                    ": answer with exactly GATEWAY-"+label+"-241026 and no tools."
                    if not conversation_id else
                    "Repeat the code in the previous response and add CONTINUED-"+label+"-241026. No tools.")
            async for e in transport.generate(session_id=label,conversation_id=cid,
                                               prompt=prompt,request_id="test-"+label):
                if e.type=="text_delta":parts.append(e.content)
                if e.type=="text_replace":parts=[e.content]
                if e.type=="submitted" and e.conversation_id:cid=e.conversation_id
                if e.type=="completed":
                    final=e;cid=e.conversation_id
            answer="".join(parts)
            expected=("CONTINUED-"+label if conversation_id else "GATEWAY-"+label)
            result={"label":label,"success":expected in answer,"events":len(parts),
                    "duration":round(time.monotonic()-started,2),
                    "cid":cid,"final":bool(final),"answer_preview":answer[:160]}
            print(json.dumps(result,ensure_ascii=False),flush=True)
            return cid,answer
        (a,b)=await asyncio.gather(run("A"),run("B"))
        await run("A",a[0])
    finally:
        await transport.close()
asyncio.run(go())
