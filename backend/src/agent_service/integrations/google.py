"""Google OAuth lifecycle. No network work occurs at service startup."""

import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import urlencode

import httpx
from starlette.concurrency import run_in_threadpool

from agent_service.integrations.settings import EMAIL, READ_SCOPES, GoogleSettings
from agent_service.integrations.store import IntegrationStore
from agent_service.storage import StoreError


class GoogleManager:
    def __init__(self, db, *, settings_loader=GoogleSettings.load, transport=None):
        self.store = IntegrationStore(db)
        self.settings_loader, self.transport = settings_loader, transport
        self.locks = {}
        self.revoking = set()

    async def db(self, method, *args):
        return await run_in_threadpool(method, *args)

    async def status(self, owner):
        status = await self.db(self.store.status, owner)
        try:
            self.settings_loader()
        except StoreError:
            status["status"] = "not_configured"
        return status

    async def http(self, method, url, *, limit=2_000_000, **kwargs):
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(method, url, **kwargs) as response:
                    parts, size = [], 0
                    async for part in response.aiter_bytes():
                        size += len(part)
                        if size > limit:
                            raise StoreError("integration_response_too_large")
                        parts.append(part)
                    try:
                        data = json.loads(b"".join(parts)) if parts else {}
                    except ValueError:
                        data = {}
                    return response.status_code, data if isinstance(data, dict) else {}
        except httpx.HTTPError:
            raise StoreError("integration_unavailable") from None

    async def connect(self, owner):
        if owner in self.revoking:
            raise StoreError("integration_busy")
        settings = self.settings_loader()
        state, cookie, verifier = await self.db(self.store.begin, owner, READ_SCOPES)
        if owner in self.revoking:
            raise StoreError("integration_busy")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": settings.client_id,
                "redirect_uri": settings.redirect_uri,
                "response_type": "code",
                "scope": " ".join(READ_SCOPES),
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return url, cookie

    async def callback(self, state, cookie, code, error):
        attempt = await self.db(self.store.consume, state, cookie)
        if attempt["owner_id"] in self.revoking:
            raise StoreError("integration_busy")
        if error:
            raise StoreError("oauth_denied" if error == "access_denied" else "oauth_failed")
        if not code or len(code) > 4096:
            raise StoreError("oauth_invalid")
        settings = self.settings_loader()
        async with self.locks.setdefault(attempt["owner_id"], asyncio.Lock()):
            status, tokens = await self.http(
                "POST",
                "https://oauth2.googleapis.com/token",
                limit=65536,
                data={
                    "code": code,
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "redirect_uri": settings.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": attempt["verifier"],
                },
            )
            if (
                status != 200
                or not isinstance(tokens.get("access_token"), str)
                or not tokens["access_token"]
            ):
                raise StoreError("oauth_failed")
            granted = tokens.get("scope", " ".join(json.loads(attempt["scopes"])))
            scopes = granted.split() if isinstance(granted, str) else []
            if EMAIL not in scopes:
                raise StoreError("integration_permission_required")
            status, user = await self.http(
                "GET",
                "https://www.googleapis.com/oauth2/v2/userinfo",
                limit=65536,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if (
                status != 200
                or not user.get("verified_email")
                or not isinstance(user.get("email"), str)
            ):
                raise StoreError("oauth_identity_failed")
            if not tokens.get("refresh_token"):
                raise StoreError("oauth_refresh_missing")
            await self.db(
                self.store.save,
                attempt["owner_id"],
                attempt["version"],
                self.normalize(tokens),
                scopes,
                user["email"][:320],
            )

    @staticmethod
    def normalize(tokens, previous=None):
        try:
            return {
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token")
                or (previous or {}).get("refresh_token"),
                "expires_at": time.time() + min(max(int(tokens.get("expires_in", 3600)), 0), 86400),
            }
        except (ValueError, TypeError, KeyError):
            raise StoreError("oauth_failed") from None

    async def access(self, owner, *, force=False):
        async with self.locks.setdefault(owner, asyncio.Lock()):
            row = await self.db(self.store.record, owner)
            if row["status"] != "connected" or not row["secret"]:
                raise StoreError(
                    "integration_reconnect_required"
                    if row["status"] == "reconnect_required"
                    else "integration_not_connected"
                )
            tokens = await self.db(self.store.decrypt, row["secret"])
            if force or tokens["expires_at"] < time.time() + 60:
                settings = self.settings_loader()
                status, fresh = await self.http(
                    "POST",
                    "https://oauth2.googleapis.com/token",
                    limit=65536,
                    data={
                        "client_id": settings.client_id,
                        "client_secret": settings.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                    },
                )
                if status in (400, 401):
                    await self.db(self.store.mark_reconnect, owner, row["version"])
                    raise StoreError("integration_reconnect_required")
                if status != 200 or not isinstance(fresh.get("access_token"), str):
                    raise StoreError("integration_unavailable")
                tokens = self.normalize(fresh, tokens)
                row["version"] = await self.db(
                    self.store.save,
                    owner,
                    row["version"],
                    tokens,
                    json.loads(row["scopes"]),
                    row["email"],
                )
            return tokens["access_token"], row["version"], set(json.loads(row["scopes"]))

    async def disconnect(self, owner):
        if owner in self.revoking:
            raise StoreError("integration_busy")
        self.revoking.add(owner)
        try:
            return await self._disconnect(owner)
        finally:
            self.revoking.discard(owner)

    async def _disconnect(self, owner):
        # Invalidate immediately, including refresh/requests already in flight.
        row = await self.db(self.store.disconnect, owner)
        revoked = True
        if row and row["secret"]:
            try:
                tokens = await self.db(self.store.decrypt, row["secret"])
                status, _ = await self.http(
                    "POST",
                    "https://oauth2.googleapis.com/revoke",
                    limit=65536,
                    data={"token": tokens.get("refresh_token") or tokens["access_token"]},
                )
                revoked = status in (200, 400)
            except StoreError:
                revoked = False
        return {"status": "disconnected", "revoked": revoked}

    async def query(self, owner, path, scopes, *, params=None):
        for attempt in range(2):
            token, version, granted = await self.access(owner, force=bool(attempt))
            if not set(scopes) <= granted:
                raise StoreError("integration_permission_required")
            status, data = await self.http(
                "GET",
                (
                    "https://gmail.googleapis.com/"
                    if path.startswith("gmail/")
                    else "https://www.googleapis.com/"
                )
                + path,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            await self.db(self.store.unchanged, owner, version)
            if status == 401 and not attempt:
                continue
            if status == 401:
                await self.db(self.store.mark_reconnect, owner, version)
                raise StoreError("integration_reconnect_required")
            if status == 403:
                raise StoreError("integration_permission_required")
            if status == 404:
                raise StoreError("integration_item_not_found")
            if status == 429:
                raise StoreError("integration_rate_limited")
            if status != 200:
                raise StoreError("integration_unavailable")
            return data
