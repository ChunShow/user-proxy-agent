"""Exactly-once chat publication for terminal calls, independent of model execution."""

from uuid import uuid4

from agent_service.storage import StoreError, now


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS call_reports (
            call_id TEXT PRIMARY KEY REFERENCES phone_calls(id),
            state TEXT NOT NULL DEFAULT 'queued',
            message_id TEXT UNIQUE REFERENCES messages(id),
            error_code TEXT
        );
    """)


def enqueue(db, call):
    if call["status"] in ("ended", "failed", "canceled"):
        db.execute("INSERT OR IGNORE INTO call_reports(call_id) VALUES(?)", (call["id"],))


def fallback(call):
    status = {
        "ended": "통화가 종료되었습니다.",
        "failed": "통화를 연결하지 못했습니다.",
        "canceled": "전화 요청이 취소되었습니다.",
    }[call["status"]]
    return status + " 요약을 만들지 못했습니다. 통화 내용을 확인해 주세요."


class CallReportStore:
    def __init__(self, calls):
        self.calls, self.db = calls, calls.db

    def claim(self):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT call_id FROM call_reports WHERE state='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if not job:
                return None
            call = self.calls._get(db, job["call_id"])
            db.execute("UPDATE call_reports SET state='running' WHERE call_id=?", (call["id"],))
            return call

    def complete(self, call_id, text, error_code=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("invalid_call_report")
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM call_reports WHERE call_id=?", (call_id,)).fetchone()
            if not job:
                raise StoreError("not_found", 404)
            if job["state"] == "completed":
                return job["message_id"]
            if job["state"] != "running":
                raise StoreError("report_not_running")
            call = self.calls._get(db, call_id)
            self.db._conversation(db, call["owner_id"], call["conversation_id"])
            seq = db.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE conversation_id=?",
                (call["conversation_id"],),
            ).fetchone()[0]
            message_id = str(uuid4())
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,'assistant',?,'completed',NULL)",
                (message_id, call["conversation_id"], seq, text),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now(), call["conversation_id"])
            )
            db.execute(
                "UPDATE call_reports SET state='completed',message_id=?,error_code=? "
                "WHERE call_id=?",
                (message_id, error_code, call_id),
            )
            # Existing call polling merges by version; publication must advance it.
            db.execute(
                "UPDATE phone_calls SET version=version+1,updated_at=? WHERE id=?", (now(), call_id)
            )
            return message_id

    def recover(self):
        with self.db.connection() as db:
            ids = [
                r[0] for r in db.execute("SELECT call_id FROM call_reports WHERE state='running'")
            ]
        for call_id in ids:
            call = self.calls.record(call_id)
            self.complete(call_id, report_text(call, fallback(call)), "service_restarted")


def report_text(call, text):
    import json

    subject = json.loads(call["spec"])["subject"]
    return f"{subject} · 통화 결과\n{text}"[:4000]
