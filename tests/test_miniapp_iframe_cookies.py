"""mini app 在 iframe 裡登入後 API 全 401 的修法：production cookie SameSite=None; Secure;
Partitioned，CSRF 改由 Origin 檢查補償（2026-09-13，Telegram Web 開 Mini App 實測
「無法載入早報設定」）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _prod_cookies(monkeypatch):
    """直接改模組常數，不要 importlib.reload(api.deps)：reload 會換掉 get_current_user
    等函式物件，其他測試的 dependency_overrides 會對不上（2026-09-13 害 test_router_alerts
    在整批跑時紅）。"""
    import api.deps as deps

    monkeypatch.setattr(deps, "_COOKIE_SECURE", True)
    monkeypatch.setattr(deps, "_COOKIE_SAME_SITE", "none")
    monkeypatch.setattr(deps, "_COOKIE_PARTITIONED", True)
    return deps


class TestCookieAttributes:
    def test_production_cookies_are_none_secure_partitioned(self, monkeypatch):
        deps = _prod_cookies(monkeypatch)
        app = FastAPI()

        @app.get("/login")
        async def _login(request: Request):
            res = PlainTextResponse("ok")
            deps.set_token_cookies(res, "acc", "ref")
            return res

        @app.get("/logout")
        async def _logout():
            res = PlainTextResponse("ok")
            deps.clear_token_cookies(res)
            return res

        client = TestClient(app)
        headers = client.get("/login").headers.get_list("set-cookie")
        assert len(headers) == 2
        for h in headers:
            low = h.lower()
            assert "samesite=none" in low, h
            assert "secure" in low, h
            assert "partitioned" in low, h
            assert "httponly" in low, h
        cleared = client.get("/logout").headers.get_list("set-cookie")
        assert len(cleared) == 2
        for h in cleared:
            low = h.lower()
            assert "max-age=0" in low, h
            assert "partitioned" in low and "samesite=none" in low, (
                "刪 partitioned cookie 要帶同一組屬性，否則瀏覽器不會刪"
            )

    def test_development_keeps_lax_without_partitioned(self, monkeypatch):
        import api.deps as deps

        monkeypatch.setattr(deps, "_COOKIE_SECURE", False)
        monkeypatch.setattr(deps, "_COOKIE_SAME_SITE", "lax")
        monkeypatch.setattr(deps, "_COOKIE_PARTITIONED", False)
        app = FastAPI()

        @app.get("/login")
        async def _login():
            res = PlainTextResponse("ok")
            deps.set_token_cookies(res, "acc", "ref")
            return res

        h = TestClient(app).get("/login").headers.get_list("set-cookie")[0].lower()
        assert "samesite=lax" in h and "partitioned" not in h

    def test_constants_follow_environment(self, monkeypatch):
        """常數由 ENVIRONMENT 決定（模組載入時算一次）——用子程序驗，不 reload。"""
        import os
        import subprocess
        import sys

        env = {
            **os.environ,
            "ENVIRONMENT": "production",
            "TEST_MODE": "false",
            "SECRET_KEY": os.environ.get("SECRET_KEY")
            or "test-secret-key-for-cookie-check",
            "REDIS_URL": "redis://localhost:6379/0",
            "CORS_ORIGINS": "https://a.test",
            "ALLOWED_HOSTS": "a.test",
        }
        code = (
            "import api.deps as d; "
            "print(d._COOKIE_SAME_SITE, d._COOKIE_PARTITIONED, d._COOKIE_SECURE)"
        )
        out = (
            subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        assert out == "none True True"


class TestCsrfOriginCheck:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:4173")
        from api.deps import ACCESS_TOKEN_COOKIE
        from api.middleware_setup import setup_middleware

        app = FastAPI()
        setup_middleware(app)

        @app.post("/api/x")
        async def _post():
            return PlainTextResponse("posted")

        @app.get("/api/x")
        async def _get():
            return PlainTextResponse("got")

        c = TestClient(app, base_url="http://testserver")
        c.cookie_name = ACCESS_TOKEN_COOKIE
        return c

    def test_same_origin_post_with_cookie_passes(self, client):
        client.cookies.set(client.cookie_name, "tok")
        r = client.post("/api/x", headers={"Origin": "http://testserver"})
        assert r.status_code == 200

    def test_allowed_cors_origin_passes(self, client):
        client.cookies.set(client.cookie_name, "tok")
        r = client.post("/api/x", headers={"Origin": "http://localhost:4173"})
        assert r.status_code == 200

    def test_foreign_origin_with_cookie_is_blocked(self, client):
        client.cookies.set(client.cookie_name, "tok")
        r = client.post("/api/x", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_foreign_origin_without_cookie_passes(self, client):
        # 沒帶 auth cookie（Bearer／公開端點）不是 CSRF 面，交給原本的 CORS／授權處理
        r = client.post("/api/x", headers={"Origin": "https://evil.example"})
        assert r.status_code == 200

    def test_no_origin_header_passes(self, client):
        # curl／bot 服務端呼叫沒有 Origin
        client.cookies.set(client.cookie_name, "tok")
        assert client.post("/api/x").status_code == 200

    def test_get_is_never_blocked(self, client):
        client.cookies.set(client.cookie_name, "tok")
        r = client.get("/api/x", headers={"Origin": "https://evil.example"})
        assert r.status_code == 200
