"""Bounded provider reads. Mail and event text are untrusted external data."""

import asyncio
import base64
import binascii
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import quote

from agent_service.integrations.settings import CALENDAR_LIST, CALENDAR_READ, GMAIL_READ
from agent_service.storage import StoreError


def bounded(value, limit=500):
    return value[:limit] if isinstance(value, str) else ""


def interval(start, end):
    try:
        a, b = (datetime.fromisoformat(v) for v in (start, end))
        if a.tzinfo is None or b.tzinfo is None or not timedelta(0) < b - a <= timedelta(days=31):
            raise ValueError
        return a, b
    except (ValueError, TypeError):
        raise StoreError("invalid_calendar_range", 422) from None


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "br", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def mail_body(payload):
    plain, html = [], []
    todo = [payload]
    for _ in range(100):
        if not todo:
            break
        part = todo.pop(0)
        if not isinstance(part, dict):
            continue
        todo.extend(part.get("parts", [])[:50])
        if part.get("filename"):
            continue
        kind = part.get("mimeType")
        data = part.get("body", {}).get("data", "")
        if kind not in ("text/plain", "text/html") or not isinstance(data, str):
            continue
        try:
            content = base64.urlsafe_b64decode(data + "=" * ((-len(data)) % 4)).decode(
                "utf-8", errors="replace"
            )
        except (ValueError, binascii.Error):
            continue
        (plain if kind == "text/plain" else html).append(content)
    if plain:
        return "\n".join(plain)
    parser = PlainHTML()
    parser.feed("\n".join(html))
    return "".join(parser.parts).strip()


def calendar_rows(data, kind):
    if "items" not in data and data.get("kind") != kind:
        raise StoreError("integration_invalid_response")
    rows = data.get("items", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows[:50]):
        raise StoreError("integration_invalid_response")
    return rows


def event_time(value):
    if not isinstance(value, dict):
        raise StoreError("integration_invalid_response")
    result = {
        key: bounded(value.get(key), 100)
        for key in ("date", "dateTime", "timeZone")
        if key in value
    }
    try:
        stamp = result.get("dateTime") or result.get("date")
        if not stamp:
            raise ValueError
        datetime.fromisoformat(stamp)
    except ValueError:
        raise StoreError("integration_invalid_response") from None
    return result


class GoogleQueries:
    def __init__(self, manager, owner):
        self.manager, self.owner = manager, owner

    async def calendars(self):
        data = await self.manager.query(
            self.owner,
            "calendar/v3/users/me/calendarList",
            [CALENDAR_LIST],
            params={"maxResults": 50},
        )
        rows = calendar_rows(data, "calendar#calendarList")
        return {
            "source": "Google Calendar",
            "untrusted_external_data": True,
            "items": [
                {k: r.get(k) for k in ("id", "summary", "primary", "accessRole", "timeZone")}
                for r in rows[:50]
            ],
            "truncated": bool(data.get("nextPageToken")) or len(rows) > 50,
        }

    async def events(self, start, end, calendar_id="primary"):
        interval(start, end)
        if (
            not isinstance(calendar_id, str)
            or not 1 <= len(calendar_id) <= 320
            or any(ord(c) < 32 for c in calendar_id)
        ):
            raise StoreError("invalid_calendar", 422)
        data = await self.manager.query(
            self.owner,
            f"calendar/v3/calendars/{quote(calendar_id, safe='')}/events",
            [CALENDAR_READ],
            params={
                "timeMin": start,
                "timeMax": end,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 50,
            },
        )
        rows = calendar_rows(data, "calendar#events")
        return {
            "source": "Google Calendar",
            "untrusted_external_data": True,
            "calendar_id": calendar_id,
            "time_min": start,
            "time_max": end,
            "items": [
                {
                    "id": bounded(r.get("id")),
                    "summary": bounded(r.get("summary")),
                    "start": event_time(r.get("start")) if r.get("status") != "cancelled" else {},
                    "end": event_time(r.get("end")) if r.get("status") != "cancelled" else {},
                    "status": r.get("status"),
                    "location": bounded(r.get("location")),
                    "description": bounded(r.get("description"), 2000),
                    "transparency": r.get("transparency", "opaque"),
                }
                for r in rows[:50]
            ],
            "truncated": bool(data.get("nextPageToken")) or len(rows) > 50,
        }

    async def message(self, message_id, *, full):
        if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", message_id):
            raise StoreError("invalid_email_id", 422)
        data = await self.manager.query(
            self.owner,
            f"gmail/v1/users/me/messages/{message_id}",
            [GMAIL_READ],
            params={"format": "full" if full else "metadata"},
        )
        payload = data.get("payload", {})
        headers = {
            h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])[:100]
        }
        result = {
            "id": message_id,
            "subject": bounded(headers.get("subject")),
            "from": bounded(headers.get("from")),
            "to": bounded(headers.get("to")),
            "date": bounded(headers.get("date")),
            "snippet": bounded(data.get("snippet")),
            "source": "Gmail",
            "untrusted_external_data": True,
        }
        if full:
            body = mail_body(payload)
            result.update(
                body=body[:16000], truncated=len(body) > 16000, attachments_included=False
            )
        return result

    async def read_mail(self, message_id):
        return await self.message(message_id, full=True)

    async def search_mail(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise StoreError("invalid_email_query", 422)
        _, generation, _ = await self.manager.access(self.owner)
        data = await self.manager.query(
            self.owner,
            "gmail/v1/users/me/messages",
            [GMAIL_READ],
            params={"q": query, "maxResults": 10},
        )
        # Pin this query's account generation across all metadata reads.
        await self.manager.db(self.manager.store.unchanged, self.owner, generation)
        semaphore = asyncio.Semaphore(3)

        async def detail(item):
            async with semaphore:
                return await self.message(item["id"], full=False)

        rows = data.get("messages", [])
        results = await asyncio.gather(*(detail(r) for r in rows[:10]))
        await self.manager.db(self.manager.store.unchanged, self.owner, generation)
        return {
            "source": "Gmail",
            "untrusted_external_data": True,
            "items": results,
            "truncated": bool(data.get("nextPageToken")) or len(rows) > 10,
        }
