from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from agent_service.session import check_mutation, require_owner
from agent_service.storage import StoreError

router = APIRouter(prefix="/api/integrations/google")
COOKIE = "proxy_google_oauth"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("")
async def status(request: Request):
    owner = await require_owner(request)
    return JSONResponse(await request.app.state.integrations.status(owner), headers=HEADERS)


@router.post("/connect")
async def connect(request: Request):
    check_mutation(request)
    owner = await require_owner(request)
    url, cookie = await request.app.state.integrations.connect(owner)
    response = JSONResponse({"url": url}, headers=HEADERS)
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=600,
        httponly=True,
        samesite="lax",
        path="/api/integrations/google/callback",
    )
    return response


@router.get("/callback")
async def callback(request: Request):
    try:
        await request.app.state.integrations.callback(
            request.query_params.get("state"),
            request.cookies.get(COOKIE),
            request.query_params.get("code"),
            request.query_params.get("error"),
        )
        result = "connected"
    except StoreError as error:
        result = error.code
    response = RedirectResponse("/?apps=google&result=" + result, status_code=303, headers=HEADERS)
    response.delete_cookie(COOKIE, path="/api/integrations/google/callback")
    return response


@router.post("/disconnect")
async def disconnect(request: Request):
    check_mutation(request)
    owner = await require_owner(request)
    return JSONResponse(await request.app.state.integrations.disconnect(owner), headers=HEADERS)
