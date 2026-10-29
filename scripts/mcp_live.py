"""End-to-end live MCP approval smoke test in a dedicated temporary ChatGPT tab."""
import asyncio
import json
import tempfile
from pathlib import Path
from chatbridge.store import Store
from chatbridge.engine import GatewayEngine
from chatbridge.browser_transport import BrowserTransport

PROMPT=(
 "Технический тест. Вызови подключённый Mac Control: "
 'custom_command(operation="terminal_exec", '
 'arguments={"command":"printf MCP-BRIDGE-APPROVAL-241026",'
 '"project":"workspace","timeout_seconds":10}). '
 "Это только печать текста, без изменения файлов. "
 "После выполнения верни stdout и ничего больше."
)
async def main():
 with tempfile.TemporaryDirectory() as tmp:
  store=Store(Path(tmp)/"mcp-live.db")
  transport=BrowserTransport("http://127.0.0.1:9335",hard_timeout_seconds=150,
                            verify_interval_seconds=2)
  engine=GatewayEngine(store,transport)
  await engine.start()
  sid=store.create_session(title="gateway-mcp-smoke")["session_id"]
  req,_=engine.enqueue(sid,PROMPT,"mcp-approval-live")
  try:
   await engine.wait_terminal(req["request_id"],timeout=162)
  except asyncio.TimeoutError:
   print("WAIT_TIMEOUT",flush=True)
  row=store.get_request(req["request_id"])
  approvals=list(store.db.execute("SELECT scope,result,tool,operation FROM approvals"))
  events=store.get_events(req["request_id"])
  output={"status":row["status"],"answer":row["answer"][:350],
          "mcp_marker":"MCP-BRIDGE-APPROVAL-241026" in row["answer"],
          "approvals":[list(x) for x in approvals],
          "event_types":[x["kind"] for x in events],
          "error":row["last_error"]}
  print(json.dumps(output,ensure_ascii=False),flush=True)
  await engine.stop()
  store.close()
asyncio.run(main())
