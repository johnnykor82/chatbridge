"""Durable session/request/event store. Uses brief SQLite transactions on one event loop."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from .media import ImagePayload

TERMINAL = frozenset({"completed", "failed", "interrupted"})
INFLIGHT = frozenset({"submitting", "thinking", "streaming", "tool_running", "awaiting_approval"})


class DuplicatePayloadError(ValueError):
    pass


class SessionModeError(ValueError):
    pass


SESSION_MODES = frozenset({"standard", "temporary", "continued", "persistent"})
PROJECT_MEMORIES = frozenset({"default", "project_only"})


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            memory_mode TEXT NOT NULL, created_at REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS session_rotation_guards (
            session_id TEXT NOT NULL,
            guard_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (session_id, guard_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
          );
          CREATE TABLE IF NOT EXISTS project_verifications (
            project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            memory_mode TEXT NOT NULL, observed_at REAL NOT NULL,
            source TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS session_project_verifications (
            session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL, observed_at REAL NOT NULL,
            source TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS project_create_ops (
            operation_id TEXT PRIMARY KEY, client_request_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, memory_mode TEXT NOT NULL,
            state TEXT NOT NULL, project_id TEXT,
            last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS session_conversations (
            session_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
            closed_at REAL NOT NULL, reason TEXT NOT NULL,
            PRIMARY KEY(session_id,conversation_id)
          );
          CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, conversation_id TEXT UNIQUE,
            title TEXT NOT NULL DEFAULT '', url TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS session_rotation_guards (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            guard_id TEXT NOT NULL,
            owner TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY (session_id, guard_id)
          );
          CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            client_request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
            prompt TEXT NOT NULL, status TEXT NOT NULL,
            reasoning_effort TEXT, conversation_id TEXT,
            answer TEXT NOT NULL DEFAULT '',
            last_error TEXT,
            user_message_id TEXT, assistant_message_id TEXT,
            parent_message_id TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(session_id,client_request_id)
          );
          CREATE INDEX IF NOT EXISTS idx_request_session ON requests(session_id,created_at);
          CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            message_id TEXT, request_id TEXT, role TEXT NOT NULL,
            content TEXT NOT NULL, parent_message_id TEXT,
            created_at REAL NOT NULL,
            UNIQUE(session_id,message_id)
          );
          CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id,id);
          CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, request_id TEXT NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_events_request ON events(request_id,event_id);
          CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            tool TEXT, operation TEXT, state TEXT NOT NULL, created_at REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            session_id TEXT NOT NULL, conversation_id TEXT,
            tool TEXT, operation TEXT, scope TEXT NOT NULL,
            result TEXT NOT NULL, created_at REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS media (
            media_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            request_id TEXT NOT NULL REFERENCES requests(request_id),
            role TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            content BLOB NOT NULL,
            created_at REAL NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_media_request ON media(request_id, role);
          CREATE TABLE IF NOT EXISTS image_refs (
            ref_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL REFERENCES requests(request_id),
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            asset_pointer TEXT,
            source_url TEXT,
            metadata TEXT NOT NULL,
            created_at REAL NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_image_refs_request ON image_refs(request_id);
          CREATE TABLE IF NOT EXISTS message_refs (
            session_id TEXT NOT NULL, message_id TEXT NOT NULL,
            ref_id TEXT NOT NULL, role TEXT NOT NULL, metadata TEXT NOT NULL,
            PRIMARY KEY(session_id,message_id,ref_id)
          );
          CREATE TABLE IF NOT EXISTS worker_leases (
            slot_id TEXT PRIMARY KEY, request_id TEXT,
            session_id TEXT, acquired_at REAL, heartbeat_at REAL
          );
          CREATE TABLE IF NOT EXISTS provider_rate_limit (
            provider TEXT PRIMARY KEY, paused_until REAL NOT NULL DEFAULT 0,
            last_hit REAL NOT NULL DEFAULT 0, incidents INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0
          );
        """)
        # Older deployed databases need a nondestructive schema upgrade.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(requests)")}
        if "parent_message_id" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN parent_message_id TEXT")
        if "reasoning_effort" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN reasoning_effort TEXT")
        if "conversation_id" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN conversation_id TEXT")
            # Existing records predate rotation. Preserve the best known mapping
            # before any new logical-session rollover can occur.
            self.db.execute("""UPDATE requests SET conversation_id=(
                SELECT conversation_id FROM sessions
                WHERE sessions.session_id=requests.session_id
            )""")
        session_cols = {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}
        if "mode" not in session_cols:
            self.db.execute("ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'")
        if "project_id" not in session_cols:
            self.db.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
        # A daemon crash during a browser action leaves the outcome uncertain.
        # Never treat the former 'creating' operation as permission to retry.
        self.db.execute(
            "UPDATE project_create_ops SET state='unknown', "
            "last_error='service interrupted' WHERE state='creating'"
        )
        from .cleanup_ops import setup as setup_cleanup
        setup_cleanup(self.db)
        self.db.commit()

    @staticmethod
    def _row(row) -> dict | None:
        return dict(row) if row else None

    def rate_limit_status(self, now: float | None = None) -> dict:
        """A single authenticated browser/account shares a provider quota."""
        now = time.time() if now is None else now
        row = self.db.execute(
            "SELECT * FROM provider_rate_limit WHERE provider='chatgpt'"
        ).fetchone()
        until = float(row["paused_until"]) if row else 0.0
        return {
            "provider_rate_limited": until > now,
            "paused_until": until or None,
            "retry_after": max(0, int(__import__("math").ceil(until - now))),
            "incidents": row["incidents"] if row else 0,
            "reason": row["reason"] if row else "",
        }

    def note_provider_rate_limit(
        self, retry_after: float = 0, reason: str = "provider_http_429", now: float | None = None
    ) -> dict:
        """Persist cooldown before the scheduler can begin another ChatGPT turn."""
        now = time.time() if now is None else now
        previous = self.db.execute(
            "SELECT * FROM provider_rate_limit WHERE provider='chatgpt'"
        ).fetchone()
        last_hit = previous["last_hit"] if previous else 0.0
        same_burst = bool(last_hit and now - last_hit < 2.0)
        recent = bool(last_hit and now - last_hit < 3600)
        incidents = (
            (previous["incidents"] if previous else 0)
            if same_burst
            else (min(7, (previous["incidents"] if recent else 0) + 1))
        )
        delay = min(3600.0, max(60.0 * (2 ** (incidents - 1)), retry_after))
        until = max(previous["paused_until"] if previous else 0, now + delay)
        self.db.execute(
            """INSERT INTO provider_rate_limit(provider,paused_until,last_hit,incidents,reason,updated_at)
            VALUES('chatgpt',?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET
            paused_until=excluded.paused_until,last_hit=excluded.last_hit,
            incidents=excluded.incidents,reason=excluded.reason,updated_at=excluded.updated_at""",
            (until, now, incidents, str(reason)[:100], now),
        )
        self.db.commit()
        return self.rate_limit_status(now)

    @staticmethod
    def project_request_key(name: str, memory_mode: str, client_request_id: str | None) -> str:
        if client_request_id is not None:
            return client_request_id
        # Legacy clients without an idempotency key get the same operation
        # for the same canonical name+memory, instead of implicit duplicates.
        digest = hashlib.sha256((memory_mode + "\0" + name.strip()).encode()).hexdigest()
        return "implicit:" + digest

    def get_project_operation_by_key(self, key: str) -> dict | None:
        return self._row(
            self.db.execute(
                "SELECT * FROM project_create_ops WHERE client_request_id=?", (key,)
            ).fetchone()
        )

    def get_project_operation(self, operation_id: str) -> dict | None:
        return self._row(
            self.db.execute(
                "SELECT * FROM project_create_ops WHERE operation_id=?", (operation_id,)
            ).fetchone()
        )

    def begin_project_creation(
        self, name: str, memory_mode: str, client_request_id: str | None = None
    ) -> tuple[dict, bool]:
        if memory_mode not in PROJECT_MEMORIES or not name.strip():
            raise ValueError("Invalid project name or memory mode")
        key = self.project_request_key(name, memory_mode, client_request_id)
        row = self.db.execute(
            "SELECT * FROM project_create_ops WHERE client_request_id=?", (key,)
        ).fetchone()
        if row:
            if row["name"] != name.strip() or row["memory_mode"] != memory_mode:
                raise DuplicatePayloadError("Project creation key reused for different inputs")
            return self._row(row), False
        operation_id, now = str(uuid.uuid4()), time.time()
        self.db.execute(
            "INSERT INTO project_create_ops "
            "(operation_id,client_request_id,name,memory_mode,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (operation_id, key, name.strip(), memory_mode, "creating", now, now),
        )
        self.db.commit()
        return self.get_project_operation(operation_id), True

    def mark_project_creation_unknown(self, operation_id: str, error: str) -> dict:
        self.db.execute(
            "UPDATE project_create_ops SET state='unknown',last_error=?,updated_at=? "
            "WHERE operation_id=? AND state='creating'",
            (error[:180], time.time(), operation_id),
        )
        self.db.commit()
        return self.get_project_operation(operation_id)

    def complete_project_creation(self, operation_id: str, project_id: str) -> dict:
        """Register remote project and finish operation within one SQLite transaction."""
        row = self.get_project_operation(operation_id)
        if not row or row["state"] not in ("creating", "unknown"):
            raise ValueError("No pending project creation operation")
        existing = self.get_project(project_id)
        if existing and (
            existing["name"] != row["name"] or existing["memory_mode"] != row["memory_mode"]
        ):
            raise ValueError("Project ID already registered with different metadata")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO projects(project_id,name,memory_mode,created_at) "
                "VALUES(?,?,?,?)",
                (project_id, row["name"], row["memory_mode"], time.time()),
            )
            self.db.execute(
                "UPDATE project_create_ops SET project_id=?,state='completed', "
                "last_error=NULL,updated_at=? WHERE operation_id=?",
                (project_id, time.time(), operation_id),
            )
        return self.get_project(project_id)

    def save_project(self, project_id: str, name: str, memory_mode: str) -> dict:
        if memory_mode not in PROJECT_MEMORIES:
            raise ValueError("Invalid project memory mode")
        old = self.get_project(project_id)
        if old:
            if old["name"] != name or old["memory_mode"] != memory_mode:
                raise ValueError("Project ID already exists with different metadata")
            return old
        self.db.execute(
            "INSERT INTO projects(project_id,name,memory_mode,created_at) VALUES(?,?,?,?)",
            (project_id, name, memory_mode, time.time()),
        )
        self.db.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict | None:
        return self._row(
            self.db.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        )

    def list_projects(self) -> list[dict]:
        return [
            self._row(row)
            for row in self.db.execute("SELECT * FROM projects ORDER BY created_at DESC")
        ]

    def record_project_verification(self, project_id: str, name: str, memory_mode: str) -> dict:
        """Store an observed UI snapshot only after its local record matches."""
        registered = self.get_project(project_id)
        if not registered or (registered["name"], registered["memory_mode"]) != (
            name, memory_mode
        ):
            raise ValueError("Project verification does not match registered metadata")
        self.db.execute(
            "INSERT INTO project_verifications "
            "(project_id,name,memory_mode,observed_at,source) VALUES(?,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET name=excluded.name, "
            "memory_mode=excluded.memory_mode, observed_at=excluded.observed_at, "
            "source=excluded.source",
            (project_id, name, memory_mode, time.time(), "ui_project_settings_dialog"),
        )
        self.db.commit()
        return self.get_project_verification(project_id)

    def get_project_verification(self, project_id: str) -> dict | None:
        return self._row(self.db.execute(
            "SELECT * FROM project_verifications WHERE project_id=?", (project_id,)
        ).fetchone())

    def record_session_project_verification(
        self, session_id: str, project_id: str, conversation_id: str
    ) -> dict:
        session = self.get_session(session_id)
        if not session or (session["project_id"], session["conversation_id"]) != (
            project_id, conversation_id
        ):
            raise ValueError("Session is not bound to the verified conversation")
        self.db.execute(
            "INSERT INTO session_project_verifications "
            "(session_id,project_id,conversation_id,observed_at,source) VALUES(?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id, "
            "conversation_id=excluded.conversation_id, observed_at=excluded.observed_at, "
            "source=excluded.source",
            (session_id, project_id, conversation_id, time.time(),
             "provider_conversation_project_id"),
        )
        self.db.commit()
        return self.get_session_project_verification(session_id, conversation_id)

    def get_session_project_verification(self, session_id: str, conversation_id: str | None):
        if not conversation_id:
            return None
        return self._row(self.db.execute(
            "SELECT * FROM session_project_verifications WHERE session_id=? AND conversation_id=?",
            (session_id, conversation_id),
        ).fetchone())

    def count_project_sessions(self, project_id: str) -> int:
        return self.db.execute(
            "SELECT count(*) FROM sessions WHERE project_id=?", (project_id,)
        ).fetchone()[0]

    def register_project(self, project_id: str, name: str, memory_mode: str) -> dict:
        """Register an already-created external project, without modifying ChatGPT."""
        return self.save_project(project_id, name, memory_mode)

    def session_has_requests(self, session_id: str) -> bool:
        return bool(
            self.db.execute(
                "SELECT 1 FROM requests WHERE session_id=? LIMIT 1", (session_id,)
            ).fetchone()
        )

    def rotation_history(self, session_id: str) -> list[dict]:
        return [
            self._row(row)
            for row in self.db.execute(
                "SELECT * FROM session_conversations WHERE session_id=? ORDER BY closed_at DESC",
                (session_id,),
            )
        ]

    def list_rotation_guards(self, session_id: str) -> list[dict]:
        return [
            self._row(row)
            for row in self.db.execute(
                "SELECT * FROM session_rotation_guards WHERE session_id=? ORDER BY created_at",
                (session_id,),
            )
        ]

    def put_rotation_guard(self, session_id: str, guard_id: str, owner: str, reason: str) -> dict:
        """Durable client-owned guard; idempotency key is the external work ID."""
        session = self.get_session(session_id)
        if not session:
            raise KeyError(session_id)
        if session["mode"] not in ("persistent", "continued"):
            raise SessionModeError("Rotation guard requires continued or persistent session")
        old = self.db.execute(
            "SELECT * FROM session_rotation_guards WHERE session_id=? AND guard_id=?",
            (session_id, guard_id),
        ).fetchone()
        if old:
            if old["owner"] != owner or old["reason"] != reason:
                raise SessionModeError("Rotation guard ID reused with different payload")
            return self._row(old)
        self.db.execute(
            "INSERT INTO session_rotation_guards(session_id,guard_id,owner,reason,created_at) "
            "VALUES (?,?,?,?,?)",
            (session_id, guard_id, owner, reason, time.time()),
        )
        self.db.commit()
        return self._row(self.db.execute(
            "SELECT * FROM session_rotation_guards WHERE session_id=? AND guard_id=?",
            (session_id, guard_id),
        ).fetchone())

    def release_rotation_guard(self, session_id: str, guard_id: str, owner: str) -> bool:
        if not self.get_session(session_id):
            raise KeyError(session_id)
        row = self.db.execute(
            "SELECT owner FROM session_rotation_guards WHERE session_id=? AND guard_id=?",
            (session_id, guard_id),
        ).fetchone()
        if not row:
            return False
        if row["owner"] != owner:
            raise SessionModeError("Only guard owner may release this rotation barrier")
        self.db.execute(
            "DELETE FROM session_rotation_guards WHERE session_id=? AND guard_id=?",
            (session_id, guard_id),
        )
        self.db.commit()
        return True

    def rotate_session(self, session_id: str, *, reason: str = "client_requested") -> dict:
        session = self.get_session(session_id)
        if not session:
            raise KeyError(session_id)
        if session["mode"] not in ("continued", "persistent"):
            raise SessionModeError("Only continued or persistent sessions can rotate")
        if self.list_rotation_guards(session_id):
            raise SessionModeError(
                "Rotation blocked by external references; release guards after acknowledgment"
            )
        if self.db.execute(
            "SELECT 1 FROM requests WHERE session_id=? AND status IN "
            "('queued','submitting','thinking','streaming','tool_running','awaiting_approval','unknown') LIMIT 1",
            (session_id,),
        ).fetchone():
            raise SessionModeError("Cannot rotate a session with unresolved requests")
        if session["conversation_id"]:
            self.db.execute(
                "INSERT OR IGNORE INTO session_conversations "
                "(session_id,conversation_id,closed_at,reason) VALUES(?,?,?,?)",
                (session_id, session["conversation_id"], time.time(), reason),
            )
        self.db.execute(
            "UPDATE sessions SET conversation_id=NULL,url=NULL,updated_at=? WHERE session_id=?",
            (time.time(), session_id),
        )
        self.db.commit()
        return self.get_session(session_id)

    def create_session(
        self,
        *,
        conversation_id: str | None = None,
        title: str = "",
        mode: str = "standard",
        project_id: str | None = None,
    ) -> dict:
        if mode not in SESSION_MODES:
            raise SessionModeError("mode must be temporary, continued, or persistent")
        if mode == "temporary" and (conversation_id or project_id):
            raise SessionModeError("Temporary chats cannot be imported or placed in projects")
        if project_id and not self.get_project(project_id):
            raise SessionModeError("Project must be created or registered first")

        now = time.time()
        if conversation_id:
            deleted = self.db.execute(
                "SELECT state FROM conversation_delete_ops WHERE conversation_id=? LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if deleted:
                raise SessionModeError(
                    "Conversation is subject to a deletion operation; import prohibited"
                )
            closed = self.db.execute(
                "SELECT session_id FROM session_conversations WHERE conversation_id=? LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if closed:
                raise SessionModeError(
                    "Conversation belongs to a rotated session; import of archived physical chat is unsafe"
                )
            existing = self.db.execute(
                "SELECT * FROM sessions WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            if existing:
                if existing["mode"] != mode or existing["project_id"] != project_id:
                    raise SessionModeError(
                        "Imported conversation belongs to a different mode/project"
                    )
                return self._row(existing)
        sid = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO sessions(session_id,conversation_id,title,url,created_at,updated_at,mode,project_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                sid,
                conversation_id,
                title,
                (
                    f"https://chatgpt.com/g/{project_id}/c/{conversation_id}"
                    if project_id and conversation_id
                    else (f"https://chatgpt.com/c/{conversation_id}" if conversation_id else None)
                ),
                now,
                now,
                mode,
                project_id,
            ),
        )
        self.db.commit()
        return self.get_session(sid)

    def get_session(self, session_id: str) -> dict | None:
        return self._row(
            self.db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        )

    def list_sessions(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        project_id: str | None = None,
        mode: str | None = None,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if project_id is not None:
            conditions.append("s.project_id=?")
            params.append(project_id)
        if mode is not None:
            conditions.append("s.mode=?")
            params.append(mode)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            """SELECT s.*, (SELECT status FROM requests r
                         WHERE r.session_id=s.session_id ORDER BY created_at DESC LIMIT 1) last_status,
                        (SELECT request_id FROM requests r
                         WHERE r.session_id=s.session_id AND status NOT IN
                         ('completed','failed','interrupted') ORDER BY created_at ASC LIMIT 1) active_request_id
                 FROM sessions s"""
            + where
            + " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )
        return [self._row(x) for x in self.db.execute(sql, (*params, limit, offset))]

    def record_request_conversation(self, request_id: str, conversation_id: str) -> None:
        """Snapshot a request's physical chat when the queued task starts."""
        self.db.execute(
            "UPDATE requests SET conversation_id=? WHERE request_id=? AND status='queued'",
            (conversation_id, request_id),
        )
        self.db.commit()

    def bind_conversation(
        self, session_id: str, conversation_id: str, *, request_id: str | None = None
    ) -> None:
        session = self.get_session(session_id)
        if not session or session["mode"] == "temporary":
            raise SessionModeError("Temporary chats do not bind persistent conversations")
        taken = self.db.execute(
            "SELECT session_id FROM sessions WHERE conversation_id=? AND session_id<>?",
            (conversation_id, session_id),
        ).fetchone()
        closed = self.db.execute(
            "SELECT session_id FROM session_conversations WHERE conversation_id=? LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if taken or closed:
            raise SessionModeError(
                "Physical conversation is owned by another session or already rotated"
            )
        if request_id is not None:
            request = self.get_request(request_id)
            if not request or request["session_id"] != session_id:
                raise SessionModeError("Request does not belong to conversation session")
            if request["conversation_id"] and request["conversation_id"] != conversation_id:
                raise SessionModeError("Request physical conversation is immutable")
        now = time.time()
        url = (
            f"https://chatgpt.com/g/{session['project_id']}/c/{conversation_id}"
            if session["project_id"]
            else f"https://chatgpt.com/c/{conversation_id}"
        )
        self.db.execute(
            "UPDATE sessions SET conversation_id=?, url=?,updated_at=? WHERE session_id=?",
            (conversation_id, url, now, session_id),
        )
        if request_id is not None:
            self.db.execute(
                "UPDATE requests SET conversation_id=? WHERE request_id=? AND session_id=?",
                (conversation_id, request_id, session_id),
            )
        self.db.commit()

    def enqueue(
        self,
        *,
        session_id: str,
        prompt: str,
        client_request_id: str,
        images: list[ImagePayload] | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict, bool]:
        # Explicit effort is durable even for default-high callers.
        effort = reasoning_effort or "high"
        if effort not in ("low", "medium", "high"):
            raise ValueError("Unsupported reasoning effort")
        images = images or []
        digest_input = json.dumps(
            [prompt, [image.digest for image in images]], separators=(",", ":")
        ).encode()
        digest = (
            hashlib.sha256(digest_input).hexdigest()
            if images
            else hashlib.sha256(prompt.encode()).hexdigest()
        )
        old = self.db.execute(
            "SELECT * FROM requests WHERE session_id=? AND client_request_id=?",
            (session_id, client_request_id),
        ).fetchone()
        if old:
            if old["payload_hash"] != digest or (old["reasoning_effort"] or "high") != effort:
                raise DuplicatePayloadError(
                    "client_request_id already used with different content or reasoning effort"
                )
            return self._row(old), False
        session = self.get_session(session_id)
        if self.db.execute("SELECT 1 FROM conversation_delete_ops WHERE session_id=? LIMIT 1",
                           (session_id,)).fetchone():
            raise SessionModeError("Session has pending/completed deletion; cannot send")
        if session and session["mode"] == "temporary" and self.session_has_requests(session_id):
            raise SessionModeError("Temporary sessions support one request only; create another")
        rid = str(uuid.uuid4())
        now = time.time()
        self.db.execute(
            """INSERT INTO requests(request_id,session_id,client_request_id,payload_hash,
            prompt,status,reasoning_effort,conversation_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                session_id,
                client_request_id,
                digest,
                prompt,
                "queued",
                effort,
                session["conversation_id"] if session and session["mode"] != "temporary" else None,
                now,
                now,
            ),
        )
        for image in images:
            self.save_image(rid, session_id, image, role="user", commit=False)
        self.db.commit()
        return self.get_request(rid), True

    def save_image(
        self,
        request_id: str,
        session_id: str,
        image: ImagePayload,
        *,
        role: str,
        commit: bool = True,
    ) -> str:
        media_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO media(media_id,session_id,request_id,role,filename,mime_type,sha256,content,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                media_id,
                session_id,
                request_id,
                role,
                image.name,
                image.mime_type,
                image.digest,
                image.data,
                time.time(),
            ),
        )
        if commit:
            self.db.commit()
        return media_id

    def get_media(self, media_id: str) -> dict | None:
        return self._row(
            self.db.execute("SELECT * FROM media WHERE media_id=?", (media_id,)).fetchone()
        )

    def list_media(self, request_id: str, role: str | None = None) -> list[dict]:
        query = "SELECT media_id,request_id,session_id,role,filename,mime_type,sha256,length(content) AS size FROM media WHERE request_id=?"
        params: tuple = (request_id,)
        if role:
            query += " AND role=?"
            params += (role,)
        return [
            self._row(row)
            for row in self.db.execute(query + " ORDER BY created_at,media_id", params)
        ]

    def image_payloads(self, request_id: str, role: str = "user") -> list[ImagePayload]:
        return [
            ImagePayload(name=r["filename"], mime_type=r["mime_type"], data=r["content"])
            for r in self.db.execute(
                "SELECT filename,mime_type,content FROM media WHERE request_id=? AND role=? ORDER BY created_at,media_id",
                (request_id, role),
            )
        ]

    def save_image_refs(
        self, request_id: str, session_id: str, role: str, refs: list[dict]
    ) -> None:
        with self.db:
            for ref in refs:
                # A pointer may legitimately appear in several requests; the
                # former global fingerprint silently lost the later reference.
                encoded = json.dumps(ref, ensure_ascii=False)
                exists = self.db.execute(
                    "SELECT 1 FROM image_refs WHERE request_id=? AND role=? AND metadata=?",
                    (request_id, role, encoded),
                ).fetchone()
                if exists:
                    continue
                fingerprint = hashlib.sha256(
                    (request_id + "\0" + role + "\0" + encoded).encode()
                ).hexdigest()
                self.db.execute(
                    "INSERT OR IGNORE INTO image_refs(ref_id,request_id,session_id,role,asset_pointer,source_url,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        fingerprint,
                        request_id,
                        session_id,
                        role,
                        ref.get("asset_pointer"),
                        ref.get("url"),
                        json.dumps(ref, ensure_ascii=False),
                        time.time(),
                    ),
                )

    def list_image_refs(self, request_id: str, role: str | None = None) -> list[dict]:
        sql = "SELECT ref_id,role,metadata FROM image_refs WHERE request_id=?"
        params: tuple = (request_id,)
        if role:
            sql += " AND role=?"
            params += (role,)
        return [
            {"ref_id": r["ref_id"], "role": r["role"], **json.loads(r["metadata"])}
            for r in self.db.execute(sql + " ORDER BY created_at,ref_id", params)
        ]

    def get_request(self, request_id: str) -> dict | None:
        return self._row(
            self.db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        )

    def pending(self) -> list[dict]:
        return [
            self._row(x)
            for x in self.db.execute(
                "SELECT * FROM requests WHERE status='queued' ORDER BY created_at"
            )
        ]

    def unresolved(self) -> list[dict]:
        return [
            self._row(row)
            for row in self.db.execute(
                "SELECT * FROM requests WHERE status='unknown' ORDER BY created_at"
            )
        ]

    def reconcile_after_restart(self) -> int:
        with self.db:
            cur = self.db.execute(
                "UPDATE requests SET status='unknown',updated_at=? WHERE status IN "
                "('submitting','thinking','streaming','tool_running','awaiting_approval')",
                (time.time(),),
            )
        return cur.rowcount

    def set_status(self, rid: str, status: str, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE requests SET status=?,last_error=COALESCE(?,last_error),updated_at=?"
                " WHERE request_id=?",
                (status, error, time.time(), rid),
            )

    def set_ids(
        self,
        rid: str,
        *,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> None:
        with self.db:
            if user_message_id:
                self.db.execute(
                    "UPDATE requests SET user_message_id=? WHERE request_id=?",
                    (user_message_id, rid),
                )
            if parent_message_id:
                self.db.execute(
                    "UPDATE requests SET parent_message_id=? WHERE request_id=?",
                    (parent_message_id, rid),
                )
            if assistant_message_id:
                self.db.execute(
                    "UPDATE requests SET assistant_message_id=? WHERE request_id=?",
                    (assistant_message_id, rid),
                )

    def append_answer(self, rid: str, delta: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE requests SET answer=answer||?,updated_at=? WHERE request_id=?",
                (delta, time.time(), rid),
            )

    def replace_answer(self, rid: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE requests SET answer=?,updated_at=? WHERE request_id=?",
                (value, time.time(), rid),
            )

    def append_event(self, session_id: str, rid: str, kind: str, payload: dict) -> int:
        now = time.time()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO events(session_id,request_id,kind,payload,created_at) VALUES(?,?,?,?,?)",
                (session_id, rid, kind, json.dumps(payload, ensure_ascii=False), now),
            )
        return int(cur.lastrowid)

    def get_events(self, rid: str, after: int = 0, limit: int = 200) -> list[dict]:
        return [
            {**self._row(x), "payload": json.loads(x["payload"])}
            for x in self.db.execute(
                "SELECT * FROM events WHERE request_id=? AND event_id>? ORDER BY event_id LIMIT ?",
                (rid, after, limit),
            )
        ]

    def complete_recovered_request(
        self, rid: str, sid: str, conversation_id: str, message_id: str,
        parent_message_id: str | None, answer: str, refs: list[dict],
    ) -> bool:
        """Atomic unknown->completed CAS, assets and SSE event (no resend)."""
        now = time.time()
        with self.db:
            updated = self.db.execute(
                "UPDATE requests SET status='completed',answer=?,assistant_message_id=?,"
                "updated_at=? WHERE request_id=? AND session_id=? AND conversation_id=? "
                "AND status='unknown'",
                (answer, message_id, now, rid, sid, conversation_id),
            )
            if updated.rowcount != 1:
                return False
            self.db.execute(
                "INSERT OR IGNORE INTO messages(session_id,message_id,request_id,role,"
                "content,parent_message_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, message_id, rid, "assistant", answer, parent_message_id, now),
            )
            for ref in refs:
                key = ref.get("asset_pointer") or ref.get("url") or ref.get("id")
                if not key or not isinstance(key, str):
                    continue
                encoded = json.dumps(ref, ensure_ascii=False)
                fingerprint = hashlib.sha256((rid + "\0assistant\0" + encoded).encode()).hexdigest()
                old = self.db.execute(
                    "SELECT 1 FROM image_refs WHERE request_id=? AND role='assistant' "
                    "AND metadata=?", (rid, encoded),
                ).fetchone()
                if not old:
                    self.db.execute(
                        "INSERT OR IGNORE INTO image_refs(ref_id,request_id,session_id,"
                        "role,asset_pointer,source_url,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (fingerprint, rid, sid, "assistant", ref.get("asset_pointer"),
                         ref.get("url"), encoded, now),
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO message_refs(session_id,message_id,ref_id,"
                    "role,metadata) VALUES(?,?,?,?,?)",
                    (sid, message_id, hashlib.sha256(key.encode()).hexdigest(),
                     "assistant", encoded),
                )
            payload = {
                "request_id": rid, "session_id": sid, "conversation_id": conversation_id,
                "type": "completed", "answer": answer, "recovered": True,
                "image_refs": self.list_image_refs(rid, "assistant"),
                "images": self.list_media(rid, "assistant"),
            }
            self.db.execute(
                "INSERT INTO events(session_id,request_id,kind,payload,created_at) "
                "VALUES(?,?,?,?,?)",
                (sid, rid, "completed", json.dumps(payload, ensure_ascii=False), now),
            )
        return True

    def insert_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        request_id: str | None = None,
        message_id: str | None = None,
        parent_message_id: str | None = None,
        created_at: float | None = None,
    ) -> None:
        with self.db:
            self.db.execute(
                """INSERT OR IGNORE INTO messages(session_id,message_id,request_id,role,
                   content,parent_message_id,created_at) VALUES(?,?,?,?,?,?,?)""",
                (
                    session_id,
                    message_id,
                    request_id,
                    role,
                    content,
                    parent_message_id,
                    created_at or time.time(),
                ),
            )

    def save_message_refs(
        self, session_id: str, message_id: str | None, role: str, refs: list[dict]
    ) -> None:
        """Persist metadata-only history pointers; never fabricate binary downloads."""
        if not message_id:
            return
        with self.db:
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                key = ref.get("asset_pointer") or ref.get("url") or ref.get("id")
                if not key or not isinstance(key, str):
                    continue
                fingerprint = hashlib.sha256(key.encode()).hexdigest()
                self.db.execute(
                    "INSERT OR IGNORE INTO message_refs(session_id,message_id,ref_id,role,metadata) "
                    "VALUES(?,?,?,?,?)",
                    (session_id, message_id, fingerprint, role,
                     json.dumps(ref, ensure_ascii=False)),
                )

    def list_message_refs(self, session_id: str, message_id: str | None) -> list[dict]:
        if not message_id:
            return []
        return [
            {"ref_id": row["ref_id"], "role": row["role"], **json.loads(row["metadata"])}
            for row in self.db.execute(
                "SELECT ref_id,role,metadata FROM message_refs WHERE session_id=? "
                "AND message_id=? ORDER BY ref_id", (session_id, message_id),
            )
        ]

    def context_size_hint(self, session_id: str) -> dict:
        """Approximate local text context; no provider network or tokenizer calls."""
        row = self.db.execute(
            "SELECT COUNT(*) AS messages, COALESCE(SUM(length(content)),0) AS characters "
            "FROM messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return {"messages": row["messages"], "characters": row["characters"]}

    def get_messages(self, session_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        return [
            self._row(x)
            for x in self.db.execute(
                "SELECT * FROM messages WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                (session_id, after, limit),
            )
        ]

    def add_approval(
        self,
        rid: str,
        session_id: str,
        conversation_id: str | None,
        tool: str | None,
        operation: str | None,
        scope: str,
        result: str,
    ) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO approvals(request_id,session_id,conversation_id,tool,operation,
                   scope,result,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (rid, session_id, conversation_id, tool, operation, scope, result, time.time()),
            )

    def list_approval_observations(self, session_id: str, *, limit: int = 100, after: int = 0) -> list[dict]:
        """Only records UI interactions. A click is not an executed MCP result."""
        return [
            {
                **self._row(r),
                "verified_by_provider": False,
                "evidence": "ui_click_only" if r["result"] == "clicked" else "unverified_local_record",
            }
            for r in self.db.execute(
                "SELECT * FROM approvals WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                (session_id, after, limit),
            )
        ]

    def count(self, table: str) -> int:
        if table not in {"sessions", "requests", "events", "messages", "approvals"}:
            raise ValueError("invalid table")
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def close(self):
        self.db.close()
