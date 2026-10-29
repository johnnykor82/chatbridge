"""Exercise real local HTTP gateway against live ChatGPT; no secret values printed."""
import asyncio
import json
import time
import aiohttp

BASE="http://127.0.0.1:8788"

async def main():
    async with aiohttp.ClientSession() as c:
        rows=[]
        for name in ("A","B"):
            async with c.post(BASE+"/v1/sessions",json={"title":"live-http-"+name}) as r:
                s=await r.json()
            async with c.post(BASE+f"/v1/sessions/{s['session_id']}/messages",
                json={"content":"HTTP bridge "+name+". Answer only API-LIVE-"+name+"-241026.","client_request_id":"one"}) as r:
                q=await r.json()
            rows.append((name,s,q))
        async def wait(item):
            name,s,q=item
            for _ in range(85):
                await asyncio.sleep(1)
                async with c.get(BASE+"/v1/requests/"+q["request_id"]) as r: result=await r.json()
                if result["status"] in ("completed","unknown","failed","interrupted"):
                    ok=result["status"]=="completed" and "API-LIVE-"+name+"-241026" in result["answer"]
                    print(json.dumps({"case":name,"state":result["status"],"ok":ok,
                        "cid":result.get("conversation_id"),"answer":result["answer"][:120],
                        "error":result["last_error"]},ensure_ascii=False),flush=True)
                    return s,q,result
            raise TimeoutError(name)
        out=await asyncio.gather(*(wait(r) for r in rows))
        if all(r["status"]=="completed" for (_,_,r) in out):
            s=out[0][0];cid=out[0][2]["conversation_id"]
            async with c.post(BASE+f"/v1/sessions/{s['session_id']}/sync") as r:
                synced=await r.json()
                print("SYNC",r.status,str(synced)[:250],flush=True)
            async with c.get(BASE+f"/v1/sessions/{s['session_id']}/messages") as r:
                history=await r.json()
                print("HISTORY",r.status,"messages",len(history["data"]),flush=True)
            async with c.post(BASE+"/v1/chat/completions",json={
                "model":"auto","session_id":s["session_id"],
                "messages":[{"role":"user","content":"What code did you answer before? Then add HTTP-SECOND-241026."}]}) as r:
                obj=await r.json()
                msg=obj.get("choices",[{"message":{"content":""}}])[0]["message"]["content"] if r.status==200 else str(obj)
                print("FOLLOWUP",r.status,"context",("API-LIVE-A-241026" in msg),
                      "second",("HTTP-SECOND-241026" in msg),"text",msg[:150],flush=True)

asyncio.run(main())
