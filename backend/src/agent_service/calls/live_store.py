"""Private durable delegation and confirmation records, scoped to existing calls."""

import json
import time
from uuid import uuid4

from agent_service.storage import StoreError


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS call_delegations (
            call_id TEXT NOT NULL REFERENCES phone_calls(id), id TEXT NOT NULL,
            revision INTEGER NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(call_id,id)
        );
        CREATE TABLE IF NOT EXISTS call_confirmations (
            id TEXT PRIMARY KEY, call_id TEXT NOT NULL REFERENCES phone_calls(id),
            delegation_id TEXT NOT NULL, revision INTEGER NOT NULL,
            question TEXT NOT NULL, options TEXT NOT NULL, status TEXT NOT NULL,
            answer TEXT, request_id TEXT, created_at REAL NOT NULL, expires_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_question ON call_confirmations(call_id)
            WHERE status='pending';
        CREATE TABLE IF NOT EXISTS call_activity (
            id INTEGER PRIMARY KEY, call_id TEXT NOT NULL REFERENCES phone_calls(id),
            kind TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS activity_call ON call_activity(call_id,id);
        DELETE FROM schema_version WHERE version < 3;
        INSERT OR IGNORE INTO schema_version VALUES(3);
    """)


def question_view(row):
    result = dict(row)
    result["options"] = json.loads(result["options"])
    result.pop("request_id", None)
    return result


class LiveStore:
    def __init__(self, calls):
        self.calls, self.db = calls, calls.db

    @staticmethod
    def _event(db, call, kind, content):
        db.execute(
            "INSERT INTO call_activity(call_id,kind,content,created_at) VALUES(?,?,?,?)",
            (call, kind, json.dumps(content, ensure_ascii=False), time.time()),
        )

    def _valid(self, db, call, delegation, revision):
        row = self.calls._get(db, call)
        return (
            row["status"] == "connected"
            and not row["stop_requested"]
            and bool(
                db.execute(
                    "SELECT 1 FROM call_delegations WHERE call_id=? AND id=? "
                    "AND revision=? AND status='running'",
                    (call, delegation, revision),
                ).fetchone()
            )
        )

    def current(self, call, delegation, revision):
        with self.db.connection() as db:
            return self._valid(db, call, delegation, revision)

    def begin(self, call, delegation):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.calls._get(db, call)
            if row["status"] != "connected" or row["stop_requested"]:
                return None
            if db.execute(
                "SELECT 1 FROM call_delegations WHERE call_id=? AND id=?", (call, delegation)
            ).fetchone():
                return None
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM call_delegations WHERE call_id=?", (call,)
            ).fetchone()[0]
            self._cancel(db, call)
            db.execute(
                "INSERT INTO call_delegations VALUES(?,?,?,'running',?)",
                (call, delegation, revision, time.time()),
            )
            self._event(db, call, "delegation_started", {"id": delegation, "revision": revision})
            return revision

    def _cancel(self, db, call):
        db.execute(
            "UPDATE call_delegations SET status='canceled' WHERE call_id=? AND status='running'",
            (call,),
        )
        db.execute(
            "UPDATE call_confirmations SET status='canceled' WHERE "
            "call_id=? AND status IN ('pending','answered')",
            (call,),
        )

    def cancel(self, call):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cancel(db, call)

    def ask(self, call, delegation, revision, question, options, timeout=60):
        if (
            not question.strip()
            or len(question) > 500
            or len(options) > 3
            or any(len(s) > 150 for s in options)
        ):
            raise StoreError("invalid_question", 422)
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._valid(db, call, delegation, revision):
                raise StoreError("call_question_inactive")
            prior = db.execute(
                "SELECT * FROM call_confirmations WHERE call_id=? AND "
                "delegation_id=? AND status='pending'",
                (call, delegation),
            ).fetchone()
            if prior:
                return question_view(prior)
            qid = str(uuid4())
            db.execute(
                "INSERT INTO call_confirmations VALUES(?,?,?,?,?,?,'pending',NULL,NULL,?,?)",
                (
                    qid,
                    call,
                    delegation,
                    revision,
                    question,
                    json.dumps(options, ensure_ascii=False),
                    time.time(),
                    time.time() + min(timeout, 60),
                ),
            )
            return question_view(
                db.execute("SELECT * FROM call_confirmations WHERE id=?", (qid,)).fetchone()
            )

    def _expire(self, db, call):
        db.execute(
            "UPDATE call_confirmations SET status='expired' WHERE "
            "call_id=? AND status='pending' AND expires_at<=?",
            (call, time.time()),
        )

    def question(self, call, qid):
        with self.db.connection() as db:
            self._expire(db, call)
            row = db.execute(
                "SELECT * FROM call_confirmations WHERE call_id=? AND id=?", (call, qid)
            ).fetchone()
            if not row:
                raise StoreError("not_found", 404)
            return question_view(row)

    def answer(self, owner, call, qid, answer, revision, request_id):
        if not answer.strip() or len(answer) > 2000:
            raise StoreError("invalid_answer", 422)
        # Expiration is committed even when the following answer transaction rejects.
        with self.db.connection() as db:
            self.calls._get(db, call, owner)
            self._expire(db, call)
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM call_confirmations WHERE id=? AND call_id=?", (qid, call)
            ).fetchone()
            if not row:
                raise StoreError("not_found", 404)
            if (
                row["request_id"] == request_id
                and row["answer"] == answer
                and row["revision"] == revision
            ):
                return question_view(row)
            if (
                row["status"] != "pending"
                or row["revision"] != revision
                or not self._valid(db, call, row["delegation_id"], revision)
            ):
                raise StoreError("call_question_inactive")
            db.execute(
                "UPDATE call_confirmations SET answer=?,request_id=?,status='answered' WHERE id=?",
                (answer, request_id, qid),
            )
            self._event(db, call, "user_answer_received", {"question_id": qid})
            return question_view(
                db.execute("SELECT * FROM call_confirmations WHERE id=?", (qid,)).fetchone()
            )

    def finish(self, call, delegation, revision, status):
        if status not in {"applied", "failed"}:
            raise ValueError("invalid_delegation_status")
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._valid(db, call, delegation, revision):
                return False
            db.execute(
                "UPDATE call_delegations SET status=? WHERE call_id=? AND id=?",
                (status, call, delegation),
            )
            self._event(db, call, "delegation_" + status, {"id": delegation, "revision": revision})
            db.execute(
                "UPDATE call_confirmations SET status=? WHERE call_id=? AND "
                "delegation_id=? AND status='answered'",
                (status, call, delegation),
            )
            db.execute(
                "UPDATE call_confirmations SET status='canceled' WHERE call_id=? "
                "AND delegation_id=? AND status='pending'",
                (call, delegation),
            )
            return True

    def event(self, call, kind, content):
        with self.db.connection() as db:
            self._event(db, call, kind, content)

    def activity(self, owner, call, after=0):
        with self.db.connection() as db:
            row = self.calls._get(db, call, owner)
            self._expire(db, call)
            rows = db.execute(
                "SELECT * FROM call_activity WHERE call_id=? AND id>? ORDER BY id LIMIT 101",
                (call, after),
            ).fetchall()
            has_more, rows = len(rows) > 100, rows[:100]
            questions = db.execute(
                "SELECT * FROM call_confirmations WHERE call_id=? ORDER BY created_at LIMIT 30",
                (call,),
            ).fetchall()
            return {
                "events": [dict(r) | {"content": json.loads(r["content"])} for r in rows],
                "questions": [question_view(r) for r in questions],
                "next_after": rows[-1]["id"] if rows else after,
                "has_more": has_more,
                "terminal": row["status"] in {"ended", "failed", "canceled"},
            }
