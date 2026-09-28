"""Optional loopback-only form authentication for a private sharing gateway.

Run separately from the product backend. Caddy checks /_access/verify before
proxying HTTP or WebSocket requests; this does not merge product identities.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

COOKIE = "__Host-proxy_access"
SESSION_SECONDS = 12 * 60 * 60


@dataclass(frozen=True)
class AccessConfig:
    origin: str
    password: str = field(repr=False)
    signing_key: bytes = field(repr=False)

    def __post_init__(self):
        url = urlsplit(self.origin)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
            or len(self.password) < 5
            or len(self.signing_key) < 32
        ):
            raise ValueError("Invalid private sharing configuration")

    @classmethod
    def load(cls):
        try:
            path = Path(os.environ["REMOTE_ACCESS_CONFIG_FILE"])
            if path.stat().st_mode & 0o077:
                raise ValueError
            data = json.loads(path.read_text())
            return cls(data["origin"], data["password"], bytes.fromhex(data["signing_key"]))
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("Private remote access configuration is missing or invalid") from None


def make_token(key: bytes, issued: int) -> str:
    payload = f"{issued}.{secrets.token_hex(16)}"
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def valid_token(key: bytes, token: str) -> bool:
    try:
        if len(token) > 160:
            return False
        issued, nonce, signature = token.split(".")
        age = time.time() - int(issued)
        expected = hmac.new(key, f"{issued}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return 0 <= age < SESSION_SECONDS and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>접속 · User Proxy Agent</title>
<style>
*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#f7f7f6;color:#262626;
font-family:"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;min-height:100vh;
min-height:100svh;display:flex;flex-direction:column;padding:28px 24px}
header{font-size:18px;font-weight:650;letter-spacing:-.5px}main{width:100%;max-width:380px;
margin:auto; padding:64px 0}h1{font-size:28px;letter-spacing:-1px;margin:0 0 12px;
font-weight:650}p{color:#6a6a67;font-size:15px;line-height:1.65;margin:0 0 32px}
label{display:block;font-size:14px;font-weight:600;margin-bottom:10px}input{display:block;
width:100%;font:inherit;font-size:16px;min-height:52px;border:1px solid #d5d5d1;
border-radius:8px;background:#fff;color:#262626;padding:12px 14px}
input:focus-visible,button:focus-visible{outline:3px solid #858580;outline-offset:3px}
button{width:100%;min-height:52px;border:0;border-radius:8px;background:#262626;color:#fff;
font:inherit;font-size:16px;font-weight:600;margin-top:20px;cursor:pointer}
.error{color:#b3373e;margin:12px 0 0;font-size:14px}.note{font-size:13px;margin:24px 0 0}
footer{font-size:12px;color:#6a6a67;text-align:center;line-height:1.7}
@media(max-height:560px){main{padding:36px 0}}
</style></head><body><header>User Proxy Agent</header>
<main><h1>비밀번호를 입력해 주세요</h1><p>공유받은 비밀번호로 접속할 수 있어요.</p>
<form method="post" action="/_access/login">
<label for="password">접속 비밀번호</label>
<input id="password" name="password" type="password" autocomplete="current-password"
required maxlength="128" aria-describedby="login-message" enterkeyhint="go">
<div id="login-message" class="error" role="status">MESSAGE</div>
<button type="submit">접속하기</button></form>
<p class="note">기존 아이디 입력 없이 비밀번호만 입력하면 됩니다.</p></main>
<footer>개인용 공유 페이지 · 로그인은 12시간 동안 유지됩니다.</footer></body></html>"""


def create_app(config: AccessConfig | None = None):
    config = config or AccessConfig.load()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    attempts: deque[float] = deque()
    password_digest = hashlib.sha256(config.password.encode()).digest()

    @app.middleware("http")
    async def private_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
                ),
            }
        )
        return response

    def authenticated(request):
        return valid_token(config.signing_key, request.cookies.get(COOKIE, ""))

    def same_origin(request):
        return request.headers.get("origin") == config.origin

    @app.get("/_access/login")
    async def login_page(request: Request):
        if authenticated(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(PAGE.replace("MESSAGE", ""))

    @app.post("/_access/login")
    async def login(request: Request):
        if not same_origin(request):
            return Response(status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return Response(status_code=413)
        if request.headers.get("content-type", "").split(";")[0] != (
            "application/x-www-form-urlencoded"
        ):
            return Response(status_code=415)
        now = time.monotonic()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            return HTMLResponse(
                PAGE.replace("MESSAGE", "잠시 후 다시 시도해 주세요."),
                status_code=429,
                headers={"Retry-After": "60"},
            )
        attempts.append(now)
        try:
            form = parse_qs(body.decode("utf-8"), max_num_fields=4)
            password = form.get("password", [""])[0]
        except (UnicodeError, ValueError):
            return Response(status_code=400)
        supplied = hashlib.sha256(password.encode()).digest()
        if not hmac.compare_digest(supplied, password_digest):
            return HTMLResponse(PAGE.replace("MESSAGE", "비밀번호를 확인해 주세요."))
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE,
            make_token(config.signing_key, int(time.time())),
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=SESSION_SECONDS,
        )
        return response

    @app.get("/_access/verify")
    async def verify(request: Request):
        if authenticated(request):
            return Response(status_code=204)
        if request.headers.get("x-forwarded-uri", "").startswith("/api/"):
            return JSONResponse({"error": {"code": "session_expired"}}, status_code=401)
        return RedirectResponse("/_access/login", status_code=303)

    @app.post("/_access/logout")
    async def logout(request: Request):
        if not same_origin(request):
            return Response(status_code=403)
        response = RedirectResponse("/_access/login", status_code=303)
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    return app
