"""Encrypted tokens and one-use, browser-bound OAuth transactions."""

import hashlib
import json
import os
import secrets
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

from agent_service.integrations.settings import (
    CALENDAR_LIST,
    CALENDAR_READ,
    CALENDAR_WRITE,
    GMAIL_READ,
    GMAIL_SEND,
)
from agent_service.storage import StoreError

KEY_LOCK = threading.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS google_connections (
            owner_id TEXT PRIMARY KEY, status TEXT NOT NULL, version INTEGER NOT NULL,
            email TEXT, scopes TEXT NOT NULL, secret TEXT, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS google_oauth_states (
            state_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL, browser_hash TEXT NOT NULL,
            verifier TEXT NOT NULL, scopes TEXT NOT NULL, version INTEGER NOT NULL,
            expires_at REAL NOT NULL
        );
    """)


class IntegrationStore:
    def __init__(self, db):
        self.db = db
        self.key_path = db.path.parent / "integration.key"

    def cipher(self, *, create=True):
        with KEY_LOCK:
            return self._cipher(create=create)

    def _cipher(self, *, create):
        try:
            if not self.key_path.exists():
                if not create:
                    raise StoreError("integration_key_missing")
                with self.db.connection() as db:
                    if (
                        db.execute(
                            "SELECT 1 FROM google_connections WHERE secret IS NOT NULL"
                        ).fetchone()
                        or db.execute("SELECT 1 FROM google_oauth_states").fetchone()
                    ):
                        raise StoreError("integration_key_missing")
                try:
                    fd = os.open(self.key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(Fernet.generate_key())
                except FileExistsError:
                    pass
            self.key_path.chmod(0o600)
            return Fernet(self.key_path.read_bytes())
        except (OSError, ValueError):
            raise StoreError("integration_key_unavailable") from None

    def encrypt(self, value):
        return self.cipher().encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value):
        try:
            return json.loads(self.cipher(create=False).decrypt(value.encode()))
        except (InvalidToken, ValueError):
            raise StoreError("integration_key_unavailable") from None

    def record(self, owner):
        with self.db.connection() as db:
            row = db.execute(
                "SELECT * FROM google_connections WHERE owner_id=?", (owner,)
            ).fetchone()
        return (
            dict(row)
            if row
            else {
                "owner_id": owner,
                "version": 0,
                "status": "disconnected",
                "scopes": "[]",
                "email": None,
                "secret": None,
            }
        )

    def status(self, owner):
        row = self.record(owner)
        scopes = set(json.loads(row["scopes"])) if row["status"] == "connected" else set()
        return {
            "status": row["status"],
            "email": row["email"],
            "version": row["version"],
            "calendar_read": CALENDAR_LIST in scopes
            and bool({CALENDAR_READ, CALENDAR_WRITE} & scopes),
            "gmail_read": GMAIL_READ in scopes,
            "calendar_write": CALENDAR_WRITE in scopes,
            "gmail_send": GMAIL_SEND in scopes,
        }

    def save(self, owner, version, tokens, scopes, email):
        encrypted = self.encrypt(tokens)
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT version FROM google_connections WHERE owner_id=?", (owner,)
            ).fetchone()
            if (row["version"] if row else 0) != version:
                raise StoreError("integration_changed")
            db.execute(
                "INSERT INTO google_connections VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_id) "
                "DO UPDATE SET status=excluded.status,version=excluded.version,"
                "email=excluded.email,"
                "scopes=excluded.scopes,secret=excluded.secret,updated_at=excluded.updated_at",
                (
                    owner,
                    "connected",
                    version + 1,
                    email,
                    json.dumps(list(scopes)),
                    encrypted,
                    time.time(),
                ),
            )
        return version + 1

    def mark_reconnect(self, owner, version):
        with self.db.connection() as db:
            db.execute(
                "UPDATE google_connections SET status='reconnect_required',version=version+1 "
                "WHERE owner_id=? AND version=?",
                (owner, version),
            )

    def unchanged(self, owner, version):
        row = self.record(owner)
        if row["version"] != version or row["status"] != "connected":
            raise StoreError("integration_changed")

    def disconnect(self, owner):
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM google_connections WHERE owner_id=?", (owner,)
            ).fetchone()
            db.execute(
                "INSERT INTO google_connections VALUES(?,'disconnected',1,NULL,'[]',NULL,?) "
                "ON CONFLICT(owner_id) DO UPDATE SET status='disconnected',version=version+1,"
                "email=NULL,scopes='[]',secret=NULL,updated_at=excluded.updated_at",
                (owner, time.time()),
            )
            db.execute("DELETE FROM google_oauth_states WHERE owner_id=?", (owner,))
        return dict(row) if row else None

    def begin(self, owner, scopes):
        state, cookie, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        encrypted = self.encrypt(verifier)
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT version FROM google_connections WHERE owner_id=?", (owner,)
            ).fetchone()
            db.execute(
                "DELETE FROM google_oauth_states WHERE owner_id=? OR expires_at<?",
                (owner, time.time()),
            )
            db.execute(
                "INSERT INTO google_oauth_states VALUES(?,?,?,?,?,?,?)",
                (
                    digest(state),
                    owner,
                    digest(cookie),
                    encrypted,
                    json.dumps(list(scopes)),
                    row["version"] if row else 0,
                    time.time() + 600,
                ),
            )
        return state, cookie, verifier

    def consume(self, state, cookie):
        if not state or not cookie or len(state) > 200 or len(cookie) > 200:
            raise StoreError("oauth_invalid")
        with self.db.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM google_oauth_states WHERE state_hash=?", (digest(state),)
            ).fetchone()
            if (
                not row
                or row["expires_at"] < time.time()
                or not secrets.compare_digest(row["browser_hash"], digest(cookie))
            ):
                raise StoreError("oauth_invalid")
            db.execute("DELETE FROM google_oauth_states WHERE state_hash=?", (digest(state),))
        return dict(row) | {"verifier": self.decrypt(row["verifier"])}
