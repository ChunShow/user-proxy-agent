import asyncio
import base64
import time

import httpx
import pytest

from agent_service.main import create_app
from agent_service.storage import StoreError


def setup(tmp_path, handler):
    from agent_service.integrations.google import GoogleManager
    from agent_service.integrations.settings import READ_SCOPES

    app = create_app(database_path=tmp_path / "db.sqlite3")
    app.state.store.initialize()
    g = GoogleManager(app.state.store, transport=httpx.MockTransport(handler))
    g.store.save(
        "owner",
        0,
        {"access_token": "TOKEN", "refresh_token": "REFRESH", "expires_at": time.time() + 3600},
        READ_SCOPES,
        "one@example.test",
    )
    return g


@pytest.mark.asyncio
async def test_calendar_bounds_all_day_and_external_data(tmp_path):
    from agent_service.integrations.queries import GoogleQueries

    seen = []

    def provider(r):
        seen.append(r)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "event",
                        "summary": "外部<script>",
                        "start": {"date": "2026-09-27"},
                        "end": {"date": "2026-09-28"},
                    }
                ],
                "nextPageToken": "next",
            },
        )

    q = GoogleQueries(setup(tmp_path, provider), "owner")
    result = await q.events("2026-09-27T00:00:00+09:00", "2026-09-28T00:00:00+09:00")
    assert result["items"][0]["start"] == {"date": "2026-09-27"} and result["truncated"]
    assert seen[0].url.params["singleEvents"] == "true"
    for start, end in [
        ("2026-09-27", "2026-09-28"),
        ("2026-09-27T00:00:00Z", "2027-01-01T00:00:00Z"),
        ("2026-09-28T00:00:00Z", "2026-09-27T00:00:00Z"),
    ]:
        with pytest.raises(StoreError):
            await q.events(start, end)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_email_search_and_mime_are_bounded_plain_data(tmp_path):
    from agent_service.integrations.queries import GoogleQueries

    def provider(r):
        if r.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"messages": [{"id": "abc123"}], "nextPageToken": "next"}
            )
        return httpx.Response(
            200,
            json={
                "id": "abc123",
                "snippet": "preview",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sender@example.test"},
                        {"name": "Subject", "value": "Subject"},
                    ],
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": base64.urlsafe_b64encode(
                                    b"<p>Hello</p><script>bad()</script>"
                                ).decode()
                            },
                        },
                        {
                            "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(b"Plain text").decode()},
                        },
                    ],
                },
            },
        )

    q = GoogleQueries(setup(tmp_path, provider), "owner")
    search = await q.search_mail("from:sender@example.test")
    assert search["truncated"] and search["items"][0]["subject"] == "Subject"
    read = await q.read_mail("abc123")
    assert read["body"] == "Plain text" and read["from"] == "sender@example.test"
    with pytest.raises(StoreError):
        await q.read_mail("../evil")
    with pytest.raises(StoreError):
        await q.search_mail("x" * 501)


@pytest.mark.asyncio
async def test_disconnect_discards_late_private_results(tmp_path):
    from agent_service.integrations.queries import GoogleQueries

    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(r):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"items": [{"id": "private"}]})

    g = setup(tmp_path, provider)
    q = GoogleQueries(g, "owner")
    task = asyncio.create_task(q.calendars())
    await entered.wait()
    g.store.disconnect("owner")
    release.set()
    with pytest.raises(StoreError, match="integration_changed"):
        await task
    with pytest.raises(StoreError, match="integration_not_connected"):
        await GoogleQueries(g, "other").calendars()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (403, "integration_permission_required"),
        (429, "integration_rate_limited"),
        (500, "integration_unavailable"),
    ],
)
async def test_provider_errors_are_safe(tmp_path, status, code):
    from agent_service.integrations.queries import GoogleQueries

    q = GoogleQueries(
        setup(tmp_path, lambda r: httpx.Response(status, json={"error": "PRIVATE"})), "owner"
    )
    with pytest.raises(StoreError, match=code) as e:
        await q.calendars()
    assert "PRIVATE" not in str(e.value)
