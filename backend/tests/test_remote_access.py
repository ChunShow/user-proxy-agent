from fastapi.testclient import TestClient

from agent_service.remote_access import AccessConfig, create_app


def client():
    app = create_app(AccessConfig("https://share.example", "test-password", b"x" * 32))
    return TestClient(app, base_url="https://share.example", follow_redirects=False)


def test_anonymous_navigation_gets_form_without_http_auth_popup():
    with client() as c:
        r = c.get("/_access/verify")
        assert r.status_code == 303 and r.headers["location"] == "/_access/login"
        assert "www-authenticate" not in r.headers
        r = c.get("/_access/login")
        assert r.status_code == 200
        assert "<form" in r.text and 'type="password"' in r.text
        assert "test-password" not in r.text
        assert r.headers["cache-control"] == "no-store"
        assert (
            c.get("/_access/verify", headers={"x-forwarded-uri": "/api/health"}).status_code == 401
        )


def test_password_login_secure_cookie_verify_logout_and_origin():
    with client() as c:
        assert (
            c.post(
                "/_access/login",
                data={"password": "test-password"},
                headers={"origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/_access/login",
                data={"password": "wrong"},
                headers={"origin": "https://share.example"},
            ).status_code
            == 200
        )
        assert c.get("/_access/verify").status_code == 303
        r = c.post(
            "/_access/login",
            data={"password": "test-password"},
            headers={"origin": "https://share.example"},
        )
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"].lower()
        assert all(s in cookie for s in ["secure", "httponly", "samesite=lax", "path=/"])
        assert c.get("/_access/verify").status_code == 204
        assert (
            c.post("/_access/logout", headers={"origin": "https://share.example"}).status_code
            == 303
        )
        assert c.get("/_access/verify").status_code == 303


def test_forged_expired_and_future_cookies_are_rejected():
    import time

    from agent_service.remote_access import COOKIE, make_token

    with client() as c:
        for token in [
            "forged",
            make_token(b"x" * 32, int(time.time()) - 50000),
            make_token(b"x" * 32, int(time.time()) + 50000),
        ]:
            c.cookies.set(COOKIE, token)
            assert c.get("/_access/verify").status_code == 303


def test_login_attempts_are_bounded_and_body_is_limited():
    with client() as c:
        h = {"origin": "https://share.example"}
        assert c.post("/_access/login", content=b"x" * 5000, headers=h).status_code == 413
        for _ in range(10):
            assert (
                c.post("/_access/login", data={"password": "wrong"}, headers=h).status_code == 200
            )
        r = c.post("/_access/login", data={"password": "test-password"}, headers=h)
        assert r.status_code == 429
        assert "retry-after" in r.headers
