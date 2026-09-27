"""Durable one-attempt jobs. All mutations use short SQLite transactions."""

import base64
import hashlib
import json
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_service.calls.settings import normalize_number
from agent_service.storage import StoreError, now

TERMINAL = frozenset({"ended", "failed", "canceled"})
ACTIVE_SQL = "status NOT IN ('ended','failed','canceled')"


class CallSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    destination: str
    subject: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=1500)
    opening_message: str = Field(min_length=1, max_length=500)
    questions: list[str] = Field(min_length=1, max_length=10)
    listen_first: bool = False

    @field_validator("destination")
    @classmethod
    def number(cls, v):
        return normalize_number(v)

    @field_validator("questions")
    @classmethod
    def question_lengths(cls, values):
        if any(not v.strip() or len(v) > 300 for v in values):
            raise ValueError("invalid_call_questions")
        return [v.strip() for v in values]


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS phone_calls (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            source_user_message_id TEXT NOT NULL REFERENCES messages(id),
            spec TEXT NOT NULL, fingerprint TEXT NOT NULL, provider_call_id TEXT,
            status TEXT NOT NULL DEFAULT 'preparing', dial_attempted_at REAL,
            stop_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'pending', reported_summary TEXT NOT NULL DEFAULT '',
            error_code TEXT, version INTEGER NOT NULL DEFAULT 1,
            end_report TEXT NOT NULL DEFAULT '{}', metrics TEXT NOT NULL DEFAULT '{}',
            UNIQUE(owner_id, source_user_message_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS phone_single_active ON phone_calls((1))
            WHERE status NOT IN ('ended','failed','canceled');
        CREATE INDEX IF NOT EXISTS phone_owner_conversation
            ON phone_calls(owner_id, conversation_id, created_at, id);
        CREATE TABLE IF NOT EXISTS phone_call_events (
            id INTEGER PRIMARY KEY, call_id TEXT NOT NULL REFERENCES phone_calls(id),
            created_at TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT,
            version INTEGER NOT NULL
        );
    """)
    from agent_service.calls.live_store import migrate as migrate_live

    migrate_live(db)
    from agent_service.calls.report_store import migrate as migrate_reports

    migrate_reports(db)


def view(row):
    result = {
        k: row[k]
        for k in (
            "id",
            "conversation_id",
            "source_user_message_id",
            "status",
            "created_at",
            "updated_at",
            "outcome",
            "reported_summary",
            "end_report",
            "metrics",
            "error_code",
            "version",
            "stop_requested",
        )
    }
    result["stop_requested"] = bool(result["stop_requested"])
    result.update(json.loads(row["spec"]))
    return result


class CallStore:
    def __init__(self, conversations):
        self.db = conversations

    def _view(self, db, row):
        from agent_service.calls.live_store import question_view

        questions = db.execute(
            "SELECT * FROM call_confirmations WHERE call_id=? ORDER BY created_at LIMIT 30",
            (row["id"],),
        ).fetchall()
        instructions = db.execute(
            "SELECT * FROM call_instructions WHERE call_id=? ORDER BY condition_revision",
            (row["id"],),
        ).fetchall()
        report = db.execute(
            "SELECT message_id FROM call_reports WHERE call_id=?", (row["id"],)
        ).fetchone()
        return view(row) | {
            "result_message_id": report["message_id"] if report else None,
            "confirmations": [question_view(q) for q in questions],
            "instructions": [dict(i) for i in instructions],
        }

    def _get(self, db, call_id, owner=None):
        sql, args = "SELECT * FROM phone_calls WHERE id=?", [call_id]
        if owner is not None:
            sql += " AND owner_id=?"
            args.append(owner)
        row = db.execute(sql, args).fetchone()
        if row is None:
            raise StoreError("not_found", 404)
        return dict(row)

    def record(self, call_id):
        with self.db.connection() as db:
            return self._get(db, call_id)

    def get(self, owner, call_id):
        with self.db.connection() as db:
            return self._view(db, self._get(db, call_id, owner))

    def register(self, owner, cid, uid, spec):
        payload = spec.model_dump_json()
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.db._conversation(db, owner, cid)
            anchor = db.execute(
                "SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND role='user'",
                (uid, cid),
            ).fetchone()
            if not anchor:
                raise StoreError("not_found", 404)
            prior = db.execute(
                "SELECT * FROM phone_calls WHERE owner_id=? AND source_user_message_id=?",
                (owner, uid),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise StoreError("call_request_conflict")
                return view(prior)
            if db.execute(f"SELECT 1 FROM phone_calls WHERE {ACTIVE_SQL}").fetchone():
                raise StoreError("call_busy")
            call_id, timestamp = str(uuid4()), now()
            db.execute(
                """INSERT INTO phone_calls
                (id,owner_id,conversation_id,source_user_message_id,spec,fingerprint,
                 created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                (call_id, owner, cid, uid, payload, fingerprint, timestamp, timestamp),
            )
            row = self._get(db, call_id)
            self._event(db, row)
            return view(row)

    @staticmethod
    def _event(db, row):
        db.execute(
            """INSERT INTO phone_call_events(call_id,created_at,status,error_code,version)
                   VALUES(?,?,?,?,?)""",
            (row["id"], row["updated_at"], row["status"], row["error_code"], row["version"]),
        )

    def _update(self, db, call_id, changes):
        allowed = {
            "condition_revision",
            "status",
            "provider_call_id",
            "dial_attempted_at",
            "stop_requested",
            "outcome",
            "reported_summary",
            "end_report",
            "metrics",
            "error_code",
        }
        if not changes or not set(changes) <= allowed:
            raise ValueError("invalid_call_update")
        changes = changes | {"updated_at": now()}
        db.execute(
            "UPDATE phone_calls SET "
            + ",".join(f"{k}=?" for k in changes)
            + ",version=version+1 WHERE id=?",
            [*changes.values(), call_id],
        )
        row = self._get(db, call_id)
        self._event(db, row)
        from agent_service.calls.report_store import enqueue

        enqueue(db, row)
        return row

    def update(self, call_id, **changes):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, call_id)
            if row["status"] in TERMINAL:
                return view(row)
            return view(self._update(db, call_id, changes))

    def claim_dial(self, call_id):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, call_id)
            if (
                row["status"] != "preparing"
                or row["stop_requested"]
                or row["dial_attempted_at"] is not None
            ):
                return False
            self._update(db, call_id, {"dial_attempted_at": time.time(), "status": "dialing"})
            return True

    def request_stop(self, owner, call_id):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, call_id, owner)
            if row["status"] in TERMINAL or row["stop_requested"]:
                return view(row)
            return view(self._update(db, call_id, {"stop_requested": 1}))

    def active(self, owner=None, *, raw=False):
        with self.db.connection() as db:
            sql, args = f"SELECT * FROM phone_calls WHERE {ACTIVE_SQL}", []
            if owner is not None:
                sql += " AND owner_id=?"
                args.append(owner)
            rows = db.execute(sql, args).fetchall()
            return [dict(r) if raw else self._view(db, r) for r in rows]

    def list(self, owner, cid, cursor=None):
        position = None
        if cursor:
            try:
                position = json.loads(base64.urlsafe_b64decode(cursor))
                if (
                    not isinstance(position, list)
                    or len(position) != 2
                    or not all(isinstance(s, str) for s in position)
                ):
                    raise ValueError
            except (ValueError, TypeError):
                raise StoreError("invalid_request", 422) from None
        with self.db.connection() as db:
            self.db._conversation(db, owner, cid)
            sql = "SELECT * FROM phone_calls WHERE owner_id=? AND conversation_id=?"
            args = [owner, cid]
            if position:
                sql += " AND (created_at,id) < (?,?)"
                args += position
            rows = db.execute(sql + " ORDER BY created_at DESC,id DESC LIMIT 51", args).fetchall()
            cursor = (
                base64.urlsafe_b64encode(
                    json.dumps([rows[49]["created_at"], rows[49]["id"]]).encode()
                ).decode()
                if len(rows) > 50
                else None
            )
            return {"items": [self._view(db, r) for r in rows[:50]], "next_cursor": cursor}

    def user_texts(self, owner, cid, uid):
        with self.db.connection() as db:
            self.db._conversation(db, owner, cid)
            anchor = db.execute(
                "SELECT seq,text FROM messages WHERE id=? AND conversation_id=? AND role='user'",
                (uid, cid),
            ).fetchone()
            if not anchor:
                raise StoreError("not_found", 404)
            rows = db.execute(
                "SELECT text FROM messages WHERE conversation_id=? AND role='user' "
                "AND seq<=? ORDER BY seq DESC LIMIT 40",
                (cid, anchor["seq"]),
            ).fetchall()
            return anchor["text"], [r["text"] for r in rows]
