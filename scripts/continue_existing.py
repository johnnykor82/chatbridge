"""Opt-in continuation of an explicitly selected ChatBridge session."""
import argparse
import asyncio
import json
import os
import uuid

import aiohttp


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", type=uuid.UUID)
    parser.add_argument("prompt")
    parser.add_argument("--url", default=os.getenv("CHATBRIDGE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--request-id", default=str(uuid.uuid4()),
                        help="Reuse this ID only when retrying the same message")
    args = parser.parse_args()
    headers = {}
    if os.getenv("CHATBRIDGE_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["CHATBRIDGE_API_KEY"]
    async with aiohttp.ClientSession(headers=headers) as client:
        async with client.post(
            args.url.rstrip("/") + f"/v1/sessions/{args.session_id}/messages",
            json={"content": args.prompt, "client_request_id": args.request_id},
        ) as response:
            result = await response.json()
            print("SEND", response.status, json.dumps(result), flush=True)
            response.raise_for_status()
        for _ in range(85):
            await asyncio.sleep(1)
            async with client.get(args.url.rstrip("/") + "/v1/requests/" + result["request_id"]) as response:
                response.raise_for_status()
                state = await response.json()
            if state["status"] in {"completed", "failed", "unknown", "interrupted"}:
                print("RESULT", json.dumps(state, ensure_ascii=False), flush=True)
                return
        print("TIMEOUT: inspect the saved request; do not blindly resend", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
