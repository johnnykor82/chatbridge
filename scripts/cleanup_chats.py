#!/usr/bin/env python3
"""Bounded user-invoked cleanup through ChatBridge, never a direct provider API.

Default: preview only. Explicit --execute/--confirm allows at most N candidates,
one remote mutation at a time, with stable per-session idempotency keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from urllib import error, parse, request


def api(base, method, path, body=None, extra_headers=None):
    headers={"Accept":"application/json"}
    if os.environ.get("CHATBRIDGE_API_KEY"):
        headers["Authorization"]="Bearer "+os.environ["CHATBRIDGE_API_KEY"]
    if body is not None:
        headers["Content-Type"]="application/json"
        raw=json.dumps(body,ensure_ascii=False).encode("utf-8")
    else:
        raw=None
    headers.update(extra_headers or {})
    req=request.Request(base+path,data=raw,headers=headers,method=method)
    try:
        with request.urlopen(req,timeout=75) as res:
            return res.status,json.loads(res.read(300000))
    except error.HTTPError as exc:
        try:
            data=json.loads(exc.read(1200))
        except (ValueError,UnicodeError):
            data={"error":{"message":"HTTP "+str(exc.code)}}
        return exc.code,data


def main(argv=None):
    parser=argparse.ArgumentParser(description="Explicit audited ChatBridge cleanup")
    parser.add_argument("--prefix",required=True,help="Exact title prefix, e.g. example-project")
    parser.add_argument("--limit",type=int,default=5,help="Max 1..20 per run")
    parser.add_argument("--campaign-id",default="example-cleanup-v1",help="Stable idempotency namespace")
    parser.add_argument("--execute",action="store_true",help="Actually delete eligible chats")
    parser.add_argument("--confirm",default="",help="Must exactly equal DELETE- plus --prefix")
    parser.add_argument("--delay-seconds",type=int,default=25)
    parser.add_argument("--url",default="http://127.0.0.1:8787")
    args=parser.parse_args(argv)
    target=parse.urlsplit(args.url)
    if target.scheme!="http" or target.hostname not in ("127.0.0.1","localhost") or target.username or target.password:
        parser.error("Gateway must be a localhost HTTP endpoint")
    if not (3<=len(args.prefix)<=80 and 1<=args.limit<=20
            and 10<=len(args.campaign_id)<=80 and 5<=args.delay_seconds<=600):
        parser.error("Invalid prefix, limit, campaign or delay")
    if args.execute and args.confirm!="DELETE-"+args.prefix:
        parser.error("Explicit --confirm DELETE-<prefix> required")
    root=args.url.rstrip("/")
    status,plan=api(root,"POST","/v1/cleanup/plan",{"title_prefix":args.prefix,"limit":args.limit})
    if status!=200:
        print("PREVIEW_FAILED",status,plan.get("error",{}).get("message",""))
        return 2
    selections=plan["data"]
    print("CLEANUP_PREVIEW",json.dumps({
        "prefix":args.prefix,"reviewed":len(selections),"safe":sum(x["safe_candidate"] for x in selections),
        "remote":sum(bool(x["conversation_id"]) and x["safe_candidate"] for x in selections),
        "unlinked":sum(not x["conversation_id"] and x["safe_candidate"] for x in selections),
        "blocked":sum(not x["safe_candidate"] for x in selections),
        "executing":args.execute,
    },ensure_ascii=False))
    if not args.execute:
        return 0
    for i,info in enumerate(selections):
        if not info["safe_candidate"]:
            continue
        code,status_data=api(root,"GET","/v1/system/status")
        if code!=200 or status_data.get("active") or status_data.get("rate_limit",{}).get("provider_rate_limited"):
            print("STOP_BUSY_OR_COOLDOWN")
            return 3
        sid=info["session_id"]
        key="cleanup-"+hashlib.sha256((args.campaign_id+"/"+sid).encode()).hexdigest()
        if info["conversation_id"]:
            response,data=api(root,"POST",f"/v1/sessions/{sid}/deletion",{
                "confirm":"delete_remote","client_request_id":key,
                "expected_conversation_id":info["conversation_id"],"expected_title":info["title"]
            })
            if response!=200 or data.get("state")!="remote_deleted":
                print("STOP_REMOTE_UNCONFIRMED",response,data.get("state"),sid)
                return 4
            op=data["operation_id"]
            response,data=api(root,"DELETE",
                f"/v1/sessions/{sid}?operation_id={op}",
                extra_headers={"X-Confirm-Purge":"purge-local"})
        else:
            response,data=api(root,"DELETE",f"/v1/sessions/{sid}",{
                "expected_title":info["title"],"client_request_id":key,
                "confirm":"purge_unlinked"
            },extra_headers={"X-Confirm-Purge":"purge-unlinked"})
        if response!=200 or not data.get("deleted"):
            print("STOP_LOCAL_UNCONFIRMED",response,sid)
            return 5
        print("DELETED",i+1,sid)
        if i<len(selections)-1:
            time.sleep(args.delay_seconds)
    return 0


if __name__=="__main__":
    sys.exit(main())
