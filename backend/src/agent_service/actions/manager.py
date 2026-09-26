import asyncio
import base64
from email.message import EmailMessage

from starlette.concurrency import run_in_threadpool

from agent_service.actions.store import ActionStore
from agent_service.integrations.settings import CALENDAR_WRITE, GMAIL_SEND
from agent_service.storage import StoreError


class ActionManager:
    def __init__(self, db, google):
        self.store = ActionStore(db)
        self.google = google
        self.locks = {}
        self.tasks = set()
        self.closing = False

    async def db(self, method, *args):
        return await run_in_threadpool(method, *args)

    async def propose(self, context, kind, payload):
        status = await self.google.db(self.google.store.status, context.owner)
        if status["status"] != "connected":
            raise StoreError("integration_not_connected")
        return await self.db(self.store.propose, context, kind, payload, status["email"])

    async def approve(self, owner, identity, version):
        if self.closing:
            raise StoreError("actions_unavailable")
        task = asyncio.create_task(self._approve(owner, identity, version))
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    async def _approve(self, owner, identity, version):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            row = await self.db(self.store.get, owner, identity)
            if row["status"] in ("executing", "succeeded", "failed", "unknown"):
                return row
            if row["status"] != "pending":
                raise StoreError("action_inactive")
            token, generation, scopes = await self.google.access(owner)
            scope = CALENDAR_WRITE if row["kind"] == "calendar_event" else GMAIL_SEND
            if scope not in scopes:
                raise StoreError("integration_permission_required")
            row = await self.db(self.store.claim, owner, identity, version, generation)
            task = asyncio.create_task(self._execute(owner, row, token, generation))
            self.tasks.add(task)
            task.add_done_callback(self._finished)
            return row

    def _finished(self, task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _execute(self, owner, row, token, generation):
        try:
            await self.google.db(self.google.store.unchanged, owner, generation)
            payload = row["payload"]
            if row["kind"] == "calendar_event":
                url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
                body = {
                    "id": "upa" + row["id"].replace("-", ""),
                    "summary": payload["title"],
                    "start": {"dateTime": payload["start"]},
                    "end": {"dateTime": payload["end"]},
                    "description": payload["description"],
                    "location": payload["location"],
                }
            else:
                message = EmailMessage()
                message["From"] = row["account_email"]
                message["To"] = ", ".join(payload["to"])
                message["Subject"] = payload["subject"]
                message["Message-ID"] = f"<{row['id']}@user-proxy-agent.local>"
                message.set_content(payload["body"])
                url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
                body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
            status, data = await self.google.http(
                "POST", url, headers={"Authorization": f"Bearer {token}"}, json=body
            )
            if status in (200, 201) and isinstance(data.get("id"), str):
                await self.db(
                    self.store.finish,
                    owner,
                    row["id"],
                    "succeeded",
                    {"provider_id": data["id"][:300]},
                )
            elif 400 <= status < 500 and status not in (408, 409, 429):
                await self.db(
                    self.store.finish, owner, row["id"], "failed", None, "action_provider_rejected"
                )
            else:
                await self.db(
                    self.store.finish, owner, row["id"], "unknown", None, "action_delivery_unknown"
                )
        except asyncio.CancelledError:
            await self.db(
                self.store.finish, owner, row["id"], "unknown", None, "action_interrupted"
            )
            raise
        except Exception:
            await self.db(
                self.store.finish, owner, row["id"], "unknown", None, "action_delivery_unknown"
            )

    async def wait_idle(self):
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def shutdown(self):
        self.closing = True
        for task in self.tasks:
            task.cancel()
        await self.wait_idle()
        await self.db(self.store.recover)
