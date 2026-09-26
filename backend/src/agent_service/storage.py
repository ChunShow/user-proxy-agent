"""Local private SQLite storage. Methods own short, independent transactions."""

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

SESSION_AGE = 30 * 24 * 60 * 60


class StoreError(Exception):
    def __init__(self, code, status=409, **details):
        self.code, self.status, self.details = code, status, details


def now():
    return datetime.now(UTC).isoformat()


def public_message(row):
    data = {key: row[key] for key in ("id", "role", "text", "status", "error_code")}
    data["retryable"] = row["status"] in ("stopped", "interrupted") or (
        row["status"] == "failed" and row["error_code"] not in ("provider_auth", "not_configured")
    )
    return data


class ConversationStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversation_owner
                    ON conversations(owner_id, updated_at, id);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    seq INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                    status TEXT NOT NULL, error_code TEXT, UNIQUE(conversation_id, seq)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    request_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    message_id TEXT NOT NULL REFERENCES messages(id), user_message_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL,
                    error_code TEXT, PRIMARY KEY(owner_id, request_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_run ON runs(conversation_id)
                    WHERE status='streaming';
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
            """)

            from agent_service.calls.store import migrate

            migrate(db)
            from agent_service.integrations.store import migrate as migrate_integrations

            migrate_integrations(db)
            from agent_service.actions.store import migrate as migrate_actions

            migrate_actions(db)
            db.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
            db.execute("UPDATE runs SET status='interrupted' WHERE status='streaming'")

    def issue_session(self):
        token, owner = secrets.token_urlsafe(32), str(uuid4())
        with self.connection() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    owner,
                    time.time() + SESSION_AGE,
                ),
            )
        return token

    def owner_for_token(self, token):
        if not token or len(token) > 128:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT owner_id FROM sessions WHERE token_hash=? AND expires_at>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        return row["owner_id"] if row else None

    def _conversation(self, db, owner, cid):
        row = db.execute(
            "SELECT id,title,updated_at FROM conversations WHERE id=? AND owner_id=?", (cid, owner)
        ).fetchone()
        if row is None:
            raise StoreError("not_found", 404)
        return dict(row)

    def create_conversation(self, owner, cid):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO conversations VALUES(?,?,?,?,?)",
                (cid, owner, "새 대화", now(), now()),
            )
            return self._conversation(db, owner, cid)

    def list_conversations(self, owner, cursor=None):
        position = None
        if cursor:
            try:
                position = json.loads(base64.urlsafe_b64decode(cursor))
                if not isinstance(position, list) or len(position) != 2:
                    raise ValueError()
                datetime.fromisoformat(position[0])
                from uuid import UUID

                UUID(position[1])
            except (ValueError, TypeError, KeyError):
                raise StoreError("invalid_request", 422) from None
        with self.connection() as db:
            sql = "SELECT id,title,updated_at FROM conversations WHERE owner_id=?"
            args = [owner]
            if position:
                sql += " AND (updated_at, id) < (?, ?)"
                args += position
            rows = db.execute(sql + " ORDER BY updated_at DESC,id DESC LIMIT 51", args).fetchall()
        items = [dict(r) for r in rows[:50]]
        next_cursor = None
        if len(rows) > 50:
            tail = items[-1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([tail["updated_at"], tail["id"]]).encode()
            ).decode()
        return {"items": items, "next_cursor": next_cursor}

    def get_conversation(self, owner, cid, before=None):
        with self.connection() as db:
            conversation = self._conversation(db, owner, cid)
            rows = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND seq<? "
                "ORDER BY seq DESC LIMIT 51",
                (cid, before if before is not None else 9223372036854775807),
            ).fetchall()
        return {
            "conversation": conversation,
            "messages": [public_message(r) for r in reversed(rows[:50])],
            "next_cursor": str(rows[49]["seq"]) if len(rows) > 50 else None,
        }

    def begin_run(self, owner, body):
        cid, rid = str(body.conversation_id), str(body.request_id)
        fingerprint = hashlib.sha256(
            body.model_dump_json(exclude={"request_id"}).encode()
        ).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._conversation(db, owner, cid)
            previous = db.execute(
                "SELECT * FROM runs WHERE owner_id=? AND request_id=?", (owner, rid)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise StoreError("request_conflict")
                raise StoreError(
                    "request_exists",
                    conversation_id=previous["conversation_id"],
                    message_id=previous["message_id"],
                )
            if db.execute(
                "SELECT 1 FROM runs WHERE conversation_id=? AND status='streaming'", (cid,)
            ).fetchone():
                raise StoreError("conversation_busy")
            last = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 1", (cid,)
            ).fetchone()
            if body.retry_message_id:
                if (
                    not last
                    or last["id"] != str(body.retry_message_id)
                    or not public_message(last)["retryable"]
                ):
                    raise StoreError("invalid_retry", 422)
                aid = last["id"]
                user = db.execute(
                    "SELECT id FROM messages WHERE conversation_id=? AND role='user' "
                    "ORDER BY seq DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                uid = user["id"]
                db.execute(
                    "UPDATE messages SET text='',status='streaming',error_code=NULL WHERE id=?",
                    (aid,),
                )
            else:
                uid, aid = str(uuid4()), str(uuid4())
                seq = last["seq"] + 1 if last else 1
                db.execute(
                    "INSERT INTO messages VALUES(?,?,?,?,?,?,NULL)",
                    (uid, cid, seq, "user", body.content, "completed"),
                )
                db.execute(
                    "INSERT INTO messages VALUES(?,?,?,?,?,?,NULL)",
                    (aid, cid, seq + 1, "assistant", "", "streaming"),
                )
                if not last:
                    db.execute(
                        "UPDATE conversations SET title=? WHERE id=?",
                        (" ".join(body.content.split())[:40], cid),
                    )
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,NULL)",
                (rid, owner, cid, aid, uid, fingerprint, "streaming", ""),
            )
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), cid))
            # At most 80 entries are needed; preserve whole turns in history.py.
            rows = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? "
                "AND (role='user' OR status='completed') ORDER BY seq DESC LIMIT 80",
                (cid,),
            ).fetchall()
        return {
            "request_id": rid,
            "conversation_id": cid,
            "message_id": aid,
            "user_message_id": uid,
            "history": [dict(r) for r in reversed(rows)],
        }

    def save_run(self, owner, identity, text, status="streaming", error_code=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM runs WHERE owner_id=? AND request_id=? "
                "AND conversation_id=? AND status='streaming'",
                (owner, identity["request_id"], identity["conversation_id"]),
            ).fetchone()
            if row is None:
                return
            db.execute(
                "UPDATE runs SET text=?,status=?,error_code=? WHERE owner_id=? AND request_id=?",
                (text, status, error_code, owner, identity["request_id"]),
            )
            db.execute(
                "UPDATE messages SET text=?,status=?,error_code=? WHERE id=?",
                (text, status, error_code, row["message_id"]),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (now(), identity["conversation_id"]),
            )
