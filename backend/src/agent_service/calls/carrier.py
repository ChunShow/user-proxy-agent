"""ClawOps domestic REST calls. Creation is attempted exactly once."""

import re
from dataclasses import dataclass, field

import httpx

from agent_service.calls.types import CallSnapshot, DialRejected, DialUncertain, ProviderFailure

STATUS = {
    "queued": "queued",
    "ringing": "ringing",
    "in-progress": "active",
    "completed": "completed",
    "failed": "failed",
    "busy": "busy",
    "no-answer": "no_answer",
    "canceled": "canceled",
    "rejected": "failed",
}
DOMESTIC = re.compile(r"(?:0[1-9][0-9]{7,9}|1[568][0-9]{6})")
CALL_ID = re.compile(r"CA[A-Za-z0-9_-]{1,100}")


@dataclass(frozen=True)
class ClawOpsSettings:
    account: str
    api_key: str = field(repr=False)
    from_number: str
    base_url: str = "https://api.claw-ops.com"

    def __post_init__(self):
        if self.base_url != "https://api.claw-ops.com":
            raise ValueError("ClawOps API origin must be https://api.claw-ops.com")
        if not re.fullmatch(r"AC[A-Za-z0-9_-]{1,100}", self.account):
            raise ValueError("Invalid ClawOps account")
        if not self.api_key or not DOMESTIC.fullmatch(self.from_number):
            raise ValueError("Missing ClawOps key or domestic sender")


class ClawOpsControl:
    def __init__(self, settings: ClawOpsSettings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def request(self, method, suffix, *, body=None):
        try:
            return await self.client.request(
                method,
                f"{self.settings.base_url}/v1/accounts/{self.settings.account}/{suffix}",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json=body,
                timeout=12,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ProviderFailure("clawops_transport_failed") from None

    def snapshot(self, response, expected_id=""):
        try:
            response.raise_for_status()
            b = response.json()
            if (
                not CALL_ID.fullmatch(b["callId"])
                or (expected_id and b["callId"] != expected_id)
                or b["accountId"] != self.settings.account
                or b["from"] != self.settings.from_number
                or b["direction"] != "outbound"
                or not DOMESTIC.fullmatch(b["to"])
            ):
                raise ValueError
            return CallSnapshot(b["callId"], STATUS[b["status"]], b["from"], b["to"])
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise ProviderFailure("invalid_clawops_response") from None

    async def dial(self, call_id, destination):
        if not DOMESTIC.fullmatch(destination):
            raise DialRejected("clawops_requires_domestic_number")
        try:
            r = await self.request(
                "POST",
                "calls",
                body={
                    "To": destination,
                    "From": self.settings.from_number,
                    "Timeout": 25,
                },
            )
        except ProviderFailure:
            raise DialUncertain("clawops_delivery_unknown") from None
        if 400 <= r.status_code < 500:
            raise DialRejected(f"clawops_rejected_{r.status_code}")
        try:
            result = self.snapshot(r)
            if result.to_number != destination:
                raise ProviderFailure("clawops_destination_mismatch")
            return result
        except ProviderFailure:
            raise DialUncertain("clawops_delivery_unknown") from None

    async def lookup(self, external_id):
        if not CALL_ID.fullmatch(external_id):
            raise ProviderFailure("invalid_clawops_call_id")
        return self.snapshot(await self.request("GET", f"calls/{external_id}"), external_id)

    async def hangup(self, external_id):
        if not CALL_ID.fullmatch(external_id):
            raise ProviderFailure("invalid_clawops_call_id")
        r = await self.request("POST", f"calls/{external_id}", body={"Status": "completed"})
        # 404/409 may mean it already ended. Only a lookup can confirm that.
        if r.status_code not in (200, 202, 204, 404, 409):
            raise ProviderFailure("clawops_hangup_failed")
        return await self.lookup(external_id)

    async def preflight(self, destination=""):
        r = await self.request("GET", "numbers")
        try:
            r.raise_for_status()
            owned = any(n["number"] == self.settings.from_number for n in r.json()["data"])
            if not owned:
                raise ValueError
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise ProviderFailure("clawops_sender_not_verified") from None
        return {"carrier_access": True, "sender_owned": True, "phone_call_placed": False}
