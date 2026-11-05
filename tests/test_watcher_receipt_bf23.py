"""BF23-BF2: notification receipt must not wait for an assistant reply."""
import asyncio
import pytest
from chatbridge.engine import GatewayEngine
from chatbridge.model import TransportEvent
from chatbridge.store import Store

CONV="11111111-2222-4333-8444-555555555555"
DID="a"*32

class SlowAnswer:
    def __init__(self):
        self.closed=False
        self.sent=0
    async def close(self):
        pass
    async def generate(self, *, session_id, conversation_id, prompt, request_id, **kwargs):
        self.sent+=1
        try:
            yield TransportEvent("submitted",conversation_id=conversation_id or CONV,
                                 message_id="00000000-1111-4222-8333-444444444444")
            yield TransportEvent("thinking")
            await asyncio.sleep(0.7)
            yield TransportEvent("text_delta",content="actual assistant answer")
            yield TransportEvent("completed",conversation_id=conversation_id or CONV,
                                 message_id="55555555-6666-4777-8888-999999999999")
        finally:
            self.closed=True

@pytest.mark.asyncio
async def test_valid_watcher_notification_finishes_on_real_user_receipt(tmp_path):
    store=Store(tmp_path/"state.db")
    transport=SlowAnswer()
    engine=GatewayEngine(store,transport,max_active=1)
    await engine.start()
    try:
        sid=store.create_session(conversation_id=CONV)["session_id"]
        key="watcher-delivery-"+DID
        prompt=f"[MC-WATCH:{DID}]\nWatcher report for independent review."
        row,created=engine.enqueue(sid,prompt,key)
        assert created
        await engine.wait_terminal(row["request_id"],timeout=0.25)
        req=store.get_request(row["request_id"])
        assert req["status"]=="completed"
        assert req["user_message_id"]
        assert req["assistant_message_id"] is None
        assert req["answer"]==""
        assert transport.closed and engine.active==0
        events=store.get_events(row["request_id"])
        assert any(e["kind"]=="completed" for e in events)
        again,created=engine.enqueue(sid,prompt,key)
        assert not created and again["request_id"]==row["request_id"]
        assert transport.sent==1
    finally:
        await engine.stop()
        store.close()

@pytest.mark.asyncio
@pytest.mark.parametrize("key,prompt",[
 ("ordinary-turn","[MC-WATCH:"+DID+"]\nA notification-shaped ordinary prompt"),
 ("watcher-delivery-"+DID,"[MC-WATCH:"+"b"*32+"]\nMarker differs from request key"),
 ("watcher-delivery-not-hex","[MC-WATCH:not-hex]\nMalformed marker"),
])
async def test_nonmatching_key_or_marker_still_awaits_assistant(tmp_path,key,prompt):
    store=Store(tmp_path/"state.db")
    transport=SlowAnswer()
    engine=GatewayEngine(store,transport,max_active=1)
    await engine.start()
    try:
        sid=store.create_session(conversation_id=CONV)["session_id"]
        row,_=engine.enqueue(sid,prompt,key)
        with pytest.raises(asyncio.TimeoutError):
            await engine.wait_terminal(row["request_id"],timeout=0.12)
        assert store.get_request(row["request_id"])["status"] not in ("completed","unknown")
        await engine.wait_terminal(row["request_id"],timeout=2)
        assert store.get_request(row["request_id"])["assistant_message_id"]
    finally:
        await engine.stop()
        store.close()

@pytest.mark.asyncio
async def test_watcher_batch_notification_also_releases_receipt_slot(tmp_path):
    store=Store(tmp_path/"state.db")
    transport=SlowAnswer()
    engine=GatewayEngine(store,transport,max_active=1)
    await engine.start()
    try:
        sid=store.create_session(conversation_id=CONV)["session_id"]
        key="watcher-batch-"+"c"*32
        prompt=("Watcher: Multiple notifications were queued while the orchestrator was busy. "
                f"Handle EACH [MC-WATCH:{DID}] and [MC-WATCH:{'b'*32}] separately.")
        row,_=engine.enqueue(sid,prompt,key)
        await engine.wait_terminal(row["request_id"],timeout=0.25)
        assert engine.active==0 and transport.closed
        assert store.get_request(row["request_id"])["assistant_message_id"] is None
    finally:
        await engine.stop()
        store.close()
