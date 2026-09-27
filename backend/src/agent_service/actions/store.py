import json
import time
from uuid import uuid4

from pydantic import ValidationError

from agent_service.actions.schemas import EmailProposal, EventProposal
from agent_service.storage import StoreError


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS proposed_actions (
            id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            source_user_message_id TEXT NOT NULL REFERENCES messages(id),kind TEXT NOT NULL,
            payload TEXT NOT NULL,account_email TEXT NOT NULL,
            status TEXT NOT NULL,version INTEGER NOT NULL,
            result TEXT NOT NULL,error_code TEXT,created_at REAL NOT NULL,expires_at REAL NOT NULL,
            UNIQUE(owner_id,source_user_message_id,kind)
        );
    """)


def view(row):
    result = {
        k: row[k]
        for k in (
            "id",
            "conversation_id",
            "source_user_message_id",
            "kind",
            "account_email",
            "status",
            "version",
            "error_code",
            "expires_at",
        )
    }
    result["payload"] = json.loads(row["payload"])
    result["result"] = json.loads(row["result"])
    return result


class ActionStore:
    def __init__(self, db):
        self.db = db

    def _expire(self, db):
        db.execute(
            "UPDATE proposed_actions SET status='expired',version=version+1 "
            "WHERE status='pending' AND expires_at<?",
            (time.time(),),
        )

    def _get(self, db, owner, identity):
        row = db.execute(
            "SELECT * FROM proposed_actions WHERE id=? AND owner_id=?", (identity, owner)
        ).fetchone()
        if not row:
            raise StoreError("not_found", 404)
        return row

    def get(self, owner, identity):
        with self.db.connection() as db:
            self._expire(db)
            return view(self._get(db, owner, identity))

    def list(self, owner, cid):
        with self.db.connection() as db:
            self.db._conversation(db, owner, cid)
            self._expire(db)
            return [
                view(r)
                for r in db.execute(
                    "SELECT * FROM proposed_actions WHERE owner_id=? AND "
                    "conversation_id=? ORDER BY created_at",
                    (owner, cid),
                )
            ]

    def propose(self, ctx, kind, payload, email):
        try:
            schema = {"calendar_event": EventProposal, "email": EmailProposal}[kind]
            normalized = schema.model_validate(payload).model_dump()
        except (ValidationError, KeyError):
            raise StoreError("invalid_action", 422) from None
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.db._conversation(db, ctx.owner, ctx.conversation_id)
            if not db.execute(
                "SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND role='user'",
                (ctx.source_user_message_id, ctx.conversation_id),
            ).fetchone():
                raise StoreError("not_found", 404)
            prior = db.execute(
                "SELECT * FROM proposed_actions WHERE owner_id=? AND "
                "source_user_message_id=? AND kind=?",
                (ctx.owner, ctx.source_user_message_id, kind),
            ).fetchone()
            if prior:
                if prior["payload"] != encoded or prior["account_email"] != email:
                    raise StoreError("action_conflict")
                return view(prior)
            if (
                db.execute(
                    "SELECT count(*) FROM proposed_actions WHERE conversation_id=?",
                    (ctx.conversation_id,),
                ).fetchone()[0]
                >= 100
            ):
                raise StoreError("action_limit")
            identity = str(uuid4())
            stamp = time.time()
            db.execute(
                "INSERT INTO proposed_actions VALUES(?,?,?,?,?,?,?,'pending',1,'{}',NULL,?,?)",
                (
                    identity,
                    ctx.owner,
                    ctx.conversation_id,
                    ctx.source_user_message_id,
                    kind,
                    encoded,
                    email,
                    stamp,
                    stamp + 1800,
                ),
            )
            return view(self._get(db, ctx.owner, identity))

    def claim(self, owner, identity, expected, connection_version):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            row = self._get(db, owner, identity)
            self.db._conversation(db, owner, row["conversation_id"])
            if row["status"] != "pending" or row["version"] != expected:
                raise StoreError("action_inactive")
            connection = db.execute(
                "SELECT * FROM google_connections WHERE owner_id=?", (owner,)
            ).fetchone()
            if (
                not connection
                or connection["status"] != "connected"
                or connection["version"] != connection_version
                or connection["email"] != row["account_email"]
            ):
                raise StoreError("action_account_changed")
            db.execute(
                "UPDATE proposed_actions SET status='executing',version=version+1 WHERE id=?",
                (identity,),
            )
            return view(self._get(db, owner, identity))

    def finish(self, owner, identity, status, result=None, error=None):
        with self.db.connection() as db:
            db.execute(
                "UPDATE proposed_actions SET "
                "status=?,version=version+1,result=?,error_code=? WHERE id=? "
                "AND owner_id=? AND status='executing'",
                (status, json.dumps(result or {}), error, identity, owner),
            )
            return view(self._get(db, owner, identity))

    def reject(self, owner, identity, version):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            row = self._get(db, owner, identity)
            if row["status"] == "rejected":
                return view(row)
            if row["status"] != "pending" or row["version"] != version:
                raise StoreError("action_inactive")
            db.execute(
                "UPDATE proposed_actions SET status='rejected',version=version+1 WHERE id=?",
                (identity,),
            )
            return view(self._get(db, owner, identity))

    def recover(self):
        with self.db.connection() as db:
            db.execute(
                "UPDATE proposed_actions SET status='unknown',version=version+1"
                ",error_code='service_restarted' WHERE status='executing'"
            )
            self._expire(db)
