"""Browser identity for the loopback service; not an account login system."""

import os

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from agent_service.storage import SESSION_AGE

router = APIRouter()
COOKIE = "proxy_session"


def check_mutation(request: Request):
    ports = {os.getenv("WEB_PORT", "5180"), os.getenv("BACKEND_PORT", "9010")}
    allowed = {f"http://127.0.0.1:{port}" for port in ports}
    origin = request.headers.get("origin")
    if origin is not None and origin not in allowed:
        raise HTTPException(403, "forbidden_origin")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(422, "invalid_request")


async def require_owner(request: Request) -> str:
    owner = await run_in_threadpool(
        request.app.state.store.owner_for_token,
        request.cookies.get(getattr(request.app.state, "session_cookie", COOKIE)),
    )
    if owner is None:
        raise HTTPException(401, "session_expired")
    return owner


@router.post("/api/session")
async def session(request: Request):
    check_mutation(request)
    store = request.app.state.store
    owner = await run_in_threadpool(
        store.owner_for_token,
        request.cookies.get(getattr(request.app.state, "session_cookie", COOKIE)),
    )
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    if owner is None:
        token = await run_in_threadpool(store.issue_session)
        response.set_cookie(
            getattr(request.app.state, "session_cookie", COOKIE),
            token,
            httponly=True,
            samesite="strict",
            max_age=SESSION_AGE,
            path="/",
        )
    return response
