"""CB33 P08: a missing provider Send receipt must recover via verified history, never resend."""
import time

import pytest

from chatbridge.engine import GatewayEngine
from chatbridge.mock_transport import MockTransport
from chatbridge.store import Store

CID="11111111-2222-4333-8444-555555555555"
DID="a"*32
NOTICE=f"[MC-WATCH:{DID}]\nExact original watcher notification with final marker."
KEY=f"watcher-delivery-{DID}"


class History(MockTransport):
    def __init__(self, chain, *, cid=CID, complete=True):
        super().__init__(delay=0)
        self.chain=chain
        self.cid=cid
        self.complete=complete
        self.reads=0
    async def read_conversation(self, conversation_id, *, project_id=None):
        self.reads+=1
        return {"conversation_id":self.cid,"chain_complete":self.complete,"chain":self.chain}


def user(content=NOTICE, *, mid="provider-user-1", timestamp=None, parent=None):
    return {"role":"user","id":mid,"content":content,
            "parent_id":parent,"create_time":timestamp or time.time(),
            "status":"finished_successfully"}


def answer(*,uid="provider-user-1"):
    return {"role":"assistant","id":"provider-assistant-1",
            "content":"Verified result","parent_id":uid,
            "end_turn":True,"status":"finished_successfully"}


def prepared(tmp_path,prompt=NOTICE,key=KEY):
    store=Store(tmp_path/"state.db")
    sid=store.create_session(conversation_id=CID)["session_id"]
    request,_=store.enqueue(session_id=sid,prompt=prompt,client_request_id=key)
    rid=request["request_id"]
    store.set_status(rid,"unknown")
    return store,sid,rid


@pytest.mark.asyncio
async def test_missing_watcher_user_id_is_recovered_with_exact_provider_receipt(tmp_path):
    store,sid,rid=prepared(tmp_path)
    transport=History([user()])
    eng=GatewayEngine(store,transport)
    try:
        assert await eng.reconcile_request(rid) is True
        req=store.get_request(rid)
        assert req["status"]=="completed"
        assert req["user_message_id"]=="provider-user-1"
        assert req["assistant_message_id"] is None
        assert req["answer"]==""
        rows=store.get_messages(sid)
        assert len(rows)==1 and rows[0]["role"]=="user"
        assert rows[0]["request_id"]==rid and rows[0]["content"]==NOTICE
        assert sum(e["kind"]=="completed" for e in store.get_events(rid))==1
        assert await eng.reconcile_request(rid) is False
        assert transport.calls==[]
    finally: store.close()


@pytest.mark.asyncio
async def test_prior_sync_import_is_atomically_bound_without_duplicate(tmp_path):
    store,sid,rid=prepared(tmp_path)
    store.insert_message(sid,"user",NOTICE,message_id="provider-user-1")
    eng=GatewayEngine(store,History([user()]))
    try:
        assert await eng.reconcile_request(rid)
        rows=store.get_messages(sid)
        assert len(rows)==1 and rows[0]["request_id"]==rid
        assert store.get_request(rid)["user_message_id"]=="provider-user-1"
    finally:store.close()


@pytest.mark.asyncio
async def test_plain_unknown_request_with_final_assistant_recovers_without_resend(tmp_path):
    store,sid,rid=prepared(tmp_path,prompt="Very specific request",key="ordinary-key")
    eng=GatewayEngine(store,History([answer(),user("Very specific request")]))
    try:
        assert await eng.reconcile_request(rid)
        req=store.get_request(rid)
        assert req["status"]=="completed" and req["answer"]=="Verified result"
        assert req["user_message_id"]=="provider-user-1"
        assert len(store.get_messages(sid))==2
        assert eng.transport.calls==[]
    finally:store.close()


@pytest.mark.asyncio
async def test_plain_unknown_with_only_user_receipt_remains_unknown(tmp_path):
    store,sid,rid=prepared(tmp_path,prompt="Different specific request",key="ordinary-key")
    eng=GatewayEngine(store,History([user("Different specific request")]))
    try:
        assert await eng.reconcile_request(rid)
        assert store.get_request(rid)["status"]=="unknown"
        assert store.get_request(rid)["user_message_id"]=="provider-user-1"
        assert eng.transport.calls==[]
    finally:store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("history",[
    lambda:[user("nearby but wrong text")],
    lambda:[user(),user(mid="provider-user-2")],
    lambda:[{"role":"assistant",**{k:v for k,v in user().items() if k!="role"}}],
    lambda:[user(timestamp=time.time()-3600)],
])
async def test_wrong_or_ambiguous_provider_receipt_never_recovers(tmp_path,history):
    store,sid,rid=prepared(tmp_path)
    eng=GatewayEngine(store,History(history()))
    try:
        assert await eng.reconcile_request(rid) is False
        assert store.get_request(rid)["status"]=="unknown"
        assert store.get_request(rid)["user_message_id"] is None
        assert store.get_messages(sid)==[]
        assert eng.transport.calls==[]
    finally:store.close()


@pytest.mark.asyncio
async def test_mismatched_conversation_and_incomplete_branch_are_rejected(tmp_path):
    store,sid,rid=prepared(tmp_path)
    try:
        for tr in (History([user()],cid="different-chat"),
                   History([user()],complete=False)):
            assert await GatewayEngine(store,tr).reconcile_request(rid) is False
        assert store.get_request(rid)["user_message_id"] is None
    finally:store.close()


@pytest.mark.asyncio
async def test_provider_cooldown_prevents_all_history_probes(tmp_path):
    store,sid,rid=prepared(tmp_path)
    tr=History([user()])
    eng=GatewayEngine(store,tr)
    try:
        eng.note_provider_rate_limit(120,"test-outage")
        assert await eng.reconcile_request(rid) is False
        assert tr.reads==0
        assert store.get_request(rid)["status"]=="unknown"
    finally:store.close()


@pytest.mark.asyncio
async def test_conflicting_existing_local_message_is_not_rebound(tmp_path):
    store,sid,rid=prepared(tmp_path)
    store.insert_message(sid,"user","different content",message_id="provider-user-1")
    eng=GatewayEngine(store,History([user()]))
    try:
        assert await eng.reconcile_request(rid) is False
        assert store.get_request(rid)["user_message_id"] is None
    finally:store.close()


@pytest.mark.asyncio
async def test_restart_after_lost_id_recovers_without_resubmitting(tmp_path):
    store,sid,rid=prepared(tmp_path)
    # An interrupted process was submitting when connection failed.
    store.set_status(rid,"submitting")
    store.close()
    reopened=Store(tmp_path/"state.db")
    tr=History([user()])
    eng=GatewayEngine(reopened,tr)
    try:
        await eng.start()
        if eng._reconcile_task:
            await eng._reconcile_task
        assert reopened.get_request(rid)["status"]=="completed"
        assert reopened.get_request(rid)["user_message_id"]=="provider-user-1"
        assert tr.reads==1
        assert tr.calls==[]
        assert eng._recovery_monitor_task is not None
    finally:
        await eng.stop()
        reopened.close()


def test_missing_user_id_recovery_hint_has_bounded_reconcile_path(tmp_path):
    from chatbridge.recovery import recovery_hints
    store,sid,rid=prepared(tmp_path)
    try:
        hint=recovery_hints(store.get_request(rid),store.rate_limit_status())
        assert hint["unknown_reason"]=="submission_unconfirmed"
        assert hint["reconcile_after"] is not None
        assert hint["retry_after"]==0
    finally:store.close()


@pytest.mark.asyncio
async def test_prior_synced_final_assistant_reuses_same_message_id(tmp_path):
    store,sid,rid=prepared(tmp_path,prompt="Very specific request",key="ordinary-key")
    store.insert_message(sid,"assistant","Verified result",
                         message_id="provider-assistant-1",
                         parent_message_id="provider-user-1")
    eng=GatewayEngine(store,History([answer(),user("Very specific request")]))
    try:
        assert await eng.reconcile_request(rid)
        msgs=store.get_messages(sid)
        matches=[m for m in msgs if m["message_id"]=="provider-assistant-1"]
        assert len(matches)==1 and matches[0]["request_id"]==rid
        assert store.get_request(rid)["status"]=="completed"
    finally:store.close()


@pytest.mark.asyncio
async def test_conflicting_preimported_assistant_cannot_complete_request(tmp_path):
    store,sid,rid=prepared(tmp_path,prompt="Very specific request",key="ordinary-key")
    store.insert_message(sid,"assistant","forged or stale assistant text",
                         message_id="provider-assistant-1",
                         parent_message_id="provider-user-1")
    eng=GatewayEngine(store,History([answer(),user("Very specific request")]))
    try:
        await eng.reconcile_request(rid)
        assert store.get_request(rid)["status"]=="unknown"
        stored=[m for m in store.get_messages(sid)
                if m["message_id"]=="provider-assistant-1"]
        assert len(stored)==1 and stored[0]["request_id"] is None
    finally:store.close()


@pytest.mark.asyncio
async def test_existing_http_reconcile_route_recovers_unknown_receipt_without_send(tmp_path):
    from aiohttp.test_utils import TestClient,TestServer
    from chatbridge.config import Settings
    from chatbridge.server import make_app

    store,sid,rid=prepared(tmp_path)
    transport=History([user()])
    eng=GatewayEngine(store,transport)
    client=TestClient(TestServer(make_app(store,eng,Settings(transport="mock"))))
    await client.start_server()
    try:
        response=await client.post(f"/v1/requests/{rid}/reconcile")
        assert response.status==200
        result=await response.json()
        assert result["reconciled"] is True
        assert result["status"]=="completed"
        assert store.get_request(rid)["user_message_id"]=="provider-user-1"
        assert transport.calls==[]
        second=await client.post(f"/v1/requests/{rid}/reconcile")
        assert (await second.json())["reconciled"] is False
        assert len(store.get_events(rid))==1
    finally:
        await client.close()
        await eng.stop()
        store.close()
