"""Durable user instructions; delivery acknowledgement is not task success."""

import time
from uuid import uuid4

from agent_service.storage import StoreError


def migrate(db):
    for table in ("phone_calls", "call_delegations"):
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if "condition_revision" not in columns:
            db.execute(
                f"ALTER TABLE {table} ADD COLUMN condition_revision INTEGER NOT NULL DEFAULT 0"
            )
    db.executescript("""
        CREATE TABLE IF NOT EXISTS call_instructions (
            id TEXT PRIMARY KEY, call_id TEXT NOT NULL REFERENCES phone_calls(id),
            source_user_message_id TEXT NOT NULL REFERENCES messages(id),
            text TEXT NOT NULL, status TEXT NOT NULL, condition_revision INTEGER NOT NULL,
            error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(call_id,source_user_message_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_instruction ON call_instructions(call_id)
            WHERE status IN ('pending','sending');
        DELETE FROM schema_version WHERE version < 4;
        INSERT OR IGNORE INTO schema_version VALUES(4);
    """)


class InstructionStore:
    def __init__(self, calls):
        self.calls, self.db = calls, calls.db

    def prior(self, context, call, text):
        with self.db.connection() as db:
            self._authorize(db, context, call)
            return self._prior(db, context, call, text)

    def _authorize(self, db, context, call):
        row = self.calls._get(db, call, context.owner)
        if (
            row["conversation_id"] != context.conversation_id
            or not db.execute(
                "SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND role='user'",
                (context.source_user_message_id, context.conversation_id),
            ).fetchone()
        ):
            raise StoreError("not_found", 404)
        return row

    def _prior(self, db, context, call, text):
        item = db.execute(
            "SELECT * FROM call_instructions WHERE call_id=? AND source_user_message_id=?",
            (call, context.source_user_message_id),
        ).fetchone()
        if item and item["text"] != text:
            raise StoreError("instruction_conflict")
        return dict(item) if item else None

    def submit(self, context, call, text):
        from agent_service.calls.live_store import LiveStore

        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise StoreError("invalid_instruction", 422)
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._authorize(db, context, call)
            prior = self._prior(db, context, call, text)
            if prior:
                return prior
            if row["status"] != "connected" or row["stop_requested"]:
                raise StoreError("call_instruction_inactive")
            if db.execute(
                "SELECT 1 FROM call_instructions "
                "WHERE call_id=? AND status IN ('pending','sending')",
                (call,),
            ).fetchone():
                raise StoreError("instruction_busy")
            if (
                db.execute(
                    "SELECT count(*) FROM call_instructions WHERE call_id=?", (call,)
                ).fetchone()[0]
                >= 30
            ):
                raise StoreError("instruction_limit")
            identity, stamp, revision = str(uuid4()), time.time(), row["condition_revision"] + 1
            db.execute(
                "INSERT INTO call_instructions VALUES(?,?,?,?,'pending',?,NULL,?,?)",
                (identity, call, context.source_user_message_id, text, revision, stamp, stamp),
            )
            LiveStore(self.calls)._cancel(db, call)
            self.calls._update(db, call, {"condition_revision": revision})
            return dict(
                db.execute("SELECT * FROM call_instructions WHERE id=?", (identity,)).fetchone()
            )

    def transition(self, call, identity, expected, status, error=None):
        if (expected, status) not in {
            ("pending", "sending"),
            ("sending", "delivered"),
            ("pending", "not_applied"),
            ("sending", "delivery_unknown"),
        }:
            raise ValueError("invalid_instruction_transition")
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.calls._get(db, call)
            item = db.execute(
                "SELECT * FROM call_instructions WHERE call_id=? AND id=?", (call, identity)
            ).fetchone()
            if not item:
                raise StoreError("not_found", 404)
            if item["status"] != expected:
                return dict(item)
            if row["status"] != "connected" or row["stop_requested"]:
                status = "not_applied" if expected == "pending" else "delivery_unknown"
                error = "call_ended"
            db.execute(
                "UPDATE call_instructions SET status=?,error_code=?,updated_at=? WHERE id=?",
                (status, error, time.time(), identity),
            )
            self.calls._update(db, call, {"condition_revision": row["condition_revision"]})
            return dict(
                db.execute("SELECT * FROM call_instructions WHERE id=?", (identity,)).fetchone()
            )

    def abort(self, call, error="call_ended"):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.calls._get(db, call)
            changed = db.execute(
                """UPDATE call_instructions SET
                status=CASE WHEN status='pending' THEN 'not_applied' ELSE 'delivery_unknown' END,
                error_code=?,updated_at=? WHERE call_id=? AND status IN ('pending','sending')""",
                (error, time.time(), call),
            ).rowcount
            if changed:
                self.calls._update(db, call, {"condition_revision": row["condition_revision"]})

    def recover(self):
        with self.db.connection() as db:
            calls = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT call_id FROM call_instructions "
                    "WHERE status IN ('pending','sending')"
                )
            ]
        for call in calls:
            self.abort(call, "service_restarted")

    def delivered(self, call):
        with self.db.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM call_instructions "
                    "WHERE call_id=? AND status='delivered' ORDER BY condition_revision",
                    (call,),
                )
            ]


def conditions_ready(db, call):
    latest = db.execute(
        "SELECT status FROM call_instructions "
        "WHERE call_id=? ORDER BY condition_revision DESC LIMIT 1",
        (call,),
    ).fetchone()
    return latest is None or latest["status"] == "delivered"
