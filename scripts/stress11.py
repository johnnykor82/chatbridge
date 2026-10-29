"""Live stress test: ten active ChatGPT sessions and 11th queued."""
import asyncio,json,time
import aiohttp
BASE="http://127.0.0.1:8788"
async def main():
 start=time.monotonic()
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=250)) as c:
  async def register(i):
   async with c.post(BASE+"/v1/sessions",json={"title":f"stress11-{i}"}) as r:
    session=await r.json()
   prompt=f"Independent ChatBridge concurrency test #{i}. Reply with exactly STRESS11-{i}-241026. No tools."
   async with c.post(BASE+f"/v1/sessions/{session['session_id']}/messages",
     json={"content":prompt,"client_request_id":f"stress11-{i}"}) as r:
    req=await r.json()
   return i,req
  items=await asyncio.gather(*(register(i) for i in range(11)))
  await asyncio.sleep(0.8)
  async with c.get(BASE+"/metrics") as r:
   print("METRICS",await r.text(),flush=True)
  async def await_done(i,req):
   for _ in range(165):
    await asyncio.sleep(1)
    async with c.get(BASE+f"/v1/requests/{req['request_id']}") as r:row=await r.json()
    if row["status"] in ("completed","failed","unknown","interrupted"):
     ok=row["status"]=="completed" and f"STRESS11-{i}-241026" in row.get("answer","")
     return {"test":i,"ok":ok,"state":row["status"],"cid":row.get("conversation_id"),
             "error":row.get("last_error"),"duration":round(time.monotonic()-start,1)}
   return {"test":i,"ok":False,"state":"timeout"}
  results=await asyncio.gather(*(await_done(*x) for x in items))
  print("RESULTS",json.dumps(results,ensure_ascii=False),flush=True)
  print("TOTAL_OK",sum(1 for x in results if x["ok"]),len(results),flush=True)
asyncio.run(main())
