"""Durable, fail-closed ChatGPT deletion operations."""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)
TERMINAL = frozenset(("completed", "failed", "interrupted"))
PROTECTED = ("medical", "medicine", "медицин", "мед агент", "здоров",
             "telegram", "телеграм", "сводка", "коррекция", "watcher",
             "orchestrat", "исполнитель", "executor")

class CleanupBlocked(ValueError):
    pass

def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS conversation_delete_ops (
        operation_id TEXT PRIMARY KEY, client_request_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL, conversation_id TEXT,
        title TEXT NOT NULL, state TEXT NOT NULL,
        last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_delete_ops_session ON conversation_delete_ops(session_id,state)")
    db.execute("""UPDATE conversation_delete_ops SET state='unknown',
       last_error='interrupted during remote action',updated_at=?
       WHERE state='deleting'""",(time.time(),))
    db.commit()

def row(db, operation_id):
    r=db.execute("SELECT * FROM conversation_delete_ops WHERE operation_id=?",(operation_id,)).fetchone()
    return dict(r) if r else None

def session_preview(db, sid):
    r=db.execute("SELECT * FROM sessions WHERE session_id=?",(sid,)).fetchone()
    if r is None:
        return None
    r=dict(r)
    states={s["status"]:s["n"] for s in db.execute(
        "SELECT status,COUNT(*) n FROM requests WHERE session_id=? GROUP BY status",(sid,))}
    rotations=db.execute("SELECT COUNT(*) FROM session_conversations WHERE session_id=?",
                         (sid,)).fetchone()[0]
    guards=db.execute("SELECT COUNT(*) FROM session_rotation_guards WHERE session_id=?",
                      (sid,)).fetchone()[0]
    latest=db.execute("""SELECT operation_id,state FROM conversation_delete_ops
        WHERE session_id=? ORDER BY created_at DESC LIMIT 1""",(sid,)).fetchone()
    unsafe=(r["mode"]!="standard" or r["project_id"] is not None or rotations>0 or guards>0
            or any(x in r["title"].casefold() for x in PROTECTED)
            or any(s not in TERMINAL for s in states))
    return {"session_id":sid,"conversation_id":r["conversation_id"],
        "title":r["title"],"mode":r["mode"],"project_id":r["project_id"],
        "request_status_counts":states,"rotated_conversations":rotations,"rotation_guards":guards,
        "safe_candidate":not unsafe,
        "protected_reason":"protected_or_nonterminal_session" if unsafe else None,
        "latest_deletion":dict(latest) if latest else None}

def ensure_allowed(db,sid,expected_cid,expected_title):
    info=session_preview(db,sid)
    if info is None:
        raise KeyError(sid)
    if not info["safe_candidate"]:
        raise CleanupBlocked("Protected, project, rotated or unfinished session")
    if (not info["conversation_id"] or info["conversation_id"]!=expected_cid
            or not isinstance(expected_cid,str) or not UUID.fullmatch(expected_cid)):
        raise CleanupBlocked("Conversation ID must match exactly")
    if info["title"]!=expected_title:
        raise CleanupBlocked("Title must match exactly")
    return info

def begin(db,sid,key,expected_cid,expected_title):
    if not isinstance(key,str) or not 10<=len(key)<=160 or not re.fullmatch(r"[a-zA-Z0-9_.:-]+",key):
        raise CleanupBlocked("Invalid idempotency key")
    if not isinstance(expected_title,str) or not isinstance(expected_cid,str):
        raise CleanupBlocked("Exact title and conversation ID required")
    existing=db.execute("SELECT * FROM conversation_delete_ops WHERE client_request_id=?",(key,)).fetchone()
    if existing:
        if (existing["session_id"]!=sid or existing["conversation_id"]!=expected_cid
                or existing["title"]!=expected_title):
            raise CleanupBlocked("Idempotency key reused for another session")
        return dict(existing),False
    ensure_allowed(db,sid,expected_cid,expected_title)
    if db.execute("SELECT 1 FROM conversation_delete_ops WHERE session_id=?",(sid,)).fetchone():
        raise CleanupBlocked("Existing deletion operation must be reconciled")
    now=time.time()
    oid=str(uuid.uuid4())
    db.execute("""INSERT INTO conversation_delete_ops
       (operation_id,client_request_id,session_id,conversation_id,title,state,created_at,updated_at)
       VALUES(?,?,?,?,?,'deleting',?,?)""",(oid,key,sid,expected_cid,expected_title,now,now))
    db.commit()
    return row(db,oid),True

def update(db,oid,state,error=None):
    assert state in ("deleting","unknown","remote_deleted","purged")
    with db:
        db.execute("UPDATE conversation_delete_ops SET state=?,last_error=?,updated_at=? WHERE operation_id=?",
                   (state,str(error)[:160] if error else None,time.time(),oid))
    return row(db,oid)

def ensure_backup(db, db_path):
    """Make one secure SQLite snapshot each UTC day before destructive local purge.

    Never copy a live .db via raw file operations: use SQLite's online backup
    API to include its WAL. Failure blocks deletion rather than losing data.
    """
    source=Path(db_path)
    directory=source.parent / "cleanup-backups"
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=directory / (
        source.stem + ".pre-cleanup-" + datetime.now(UTC).strftime("%Y%m%d") + ".sqlite3"
    )
    if target.exists():
        return target
    fd,name=tempfile.mkstemp(prefix=".cleanup-backup-",suffix=".sqlite3",dir=directory)
    os.chmod(name,0o600)
    try:
        with os.fdopen(fd,"wb"):
            pass
        dest=sqlite3.connect(name)
        try:
            db.backup(dest)
        finally:
            dest.close()
        os.replace(name,target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target


def purge_unlinked(db, sid, *, expected_title, client_request_id):
    """Local-only cleanup when there is provably no remote conversation ID."""
    if not isinstance(expected_title, str) or not isinstance(client_request_id, str):
        raise CleanupBlocked("Exact title and idempotency key required")
    if not 10 <= len(client_request_id) <= 160 or not re.fullmatch(
        r"[a-zA-Z0-9_.:-]+", client_request_id
    ):
        raise CleanupBlocked("Invalid idempotency key")
    prior=db.execute("SELECT * FROM conversation_delete_ops WHERE client_request_id=?",
                     (client_request_id,)).fetchone()
    if prior:
        if (prior["session_id"]!=sid or prior["conversation_id"] is not None
                or prior["title"]!=expected_title or prior["state"]!="purged"):
            raise CleanupBlocked("Local purge idempotency key mismatch")
        return dict(prior)
    info=session_preview(db,sid)
    if info is None:
        raise KeyError(sid)
    if (info["conversation_id"] is not None or not info["safe_candidate"]
            or info["request_status_counts"]):
        raise CleanupBlocked(
            "Only safe, unlinked, never-submitted standard sessions can be purged locally"
        )
    if info["title"]!=expected_title:
        raise CleanupBlocked("Exact title mismatch")
    if info["latest_deletion"] is not None:
        raise CleanupBlocked("Session has an existing deletion operation")
    with db:
        _delete_owned_rows(db,sid)
        now=time.time()
        db.execute("""INSERT INTO conversation_delete_ops
          (operation_id,client_request_id,session_id,conversation_id,title,state,created_at,updated_at)
          VALUES(?,?,?,?,?,'purged',?,?)""",
          (str(uuid.uuid4()),client_request_id,sid,None,expected_title,now,now))
    return dict(db.execute("SELECT * FROM conversation_delete_ops WHERE client_request_id=?",
                           (client_request_id,)).fetchone())


def _delete_owned_rows(db, sid):
    for table in ("approvals", "events", "messages", "image_refs", "media"):
        db.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
    db.execute("""DELETE FROM tool_calls WHERE request_id IN
       (SELECT request_id FROM requests WHERE session_id=?)""",(sid,))
    db.execute("DELETE FROM worker_leases WHERE session_id=?",(sid,))
    db.execute("DELETE FROM requests WHERE session_id=?",(sid,))
    db.execute("DELETE FROM session_conversations WHERE session_id=?",(sid,))
    db.execute("DELETE FROM sessions WHERE session_id=?",(sid,))


def purge(db,sid,oid):
    op=row(db,oid)
    if not op or op["session_id"]!=sid:
        raise CleanupBlocked("Operation does not match session")
    if op["state"]=="purged":
        return op
    if op["state"]!="remote_deleted":
        raise CleanupBlocked("Remote deletion is not verified")
    if db.execute("SELECT 1 FROM sessions WHERE session_id=?",(sid,)).fetchone() is None:
        raise CleanupBlocked("Missing session before purge")
    ensure_allowed(db,sid,op["conversation_id"],op["title"])
    with db:
        _delete_owned_rows(db,sid)
        db.execute("UPDATE conversation_delete_ops SET state='purged',updated_at=? WHERE operation_id=?",
                   (time.time(),oid))
    return row(db,oid)

def candidates(db,title_prefix,limit):
    if not isinstance(title_prefix,str) or not 3<=len(title_prefix)<=100:
        raise CleanupBlocked("title_prefix must have 3..100 characters")
    if not isinstance(limit,int) or not 1<=limit<=250:
        raise CleanupBlocked("limit must be 1..250")
    rs=db.execute("SELECT session_id FROM sessions WHERE substr(title,1,?)=? "
                  "ORDER BY created_at ASC LIMIT ?",
                  (len(title_prefix),title_prefix,limit)).fetchall()
    return [session_preview(db,r["session_id"]) for r in rs]
