"""Google 登入（docs/plans/2026-09-13-google-play-twa-design.md §4）：ID token 驗證、登入／綁定／解綁
端點、config 曝露 client id、CSP、前端接線、schema。不打 Google、不打 DB。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import auth_google

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[1]
CLIENT_ID = "123-abc.apps.googleusercontent.com"


@pytest.fixture
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


def _token(priv, **over):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1089012345",
        "email": "danny@example.com",
        "email_verified": True,
        "name": "Danny",
        "picture": "https://x/y.png",
        "locale": "zh-TW",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(over)
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def gis(monkeypatch, keypair):
    priv, pub = keypair
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(
        auth_google,
        "_jwks",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda t: SimpleNamespace(key=pub)
        ),
    )
    return priv


class TestVerify:
    def test_valid_token(self, gis):
        ident = auth_google.verify_id_token(_token(gis))
        assert ident.sub == "1089012345" and ident.email == "danny@example.com"
        assert (
            ident.email_verified and ident.locale == "zh-TW" and ident.name == "Danny"
        )

    def test_wrong_audience(self, gis):
        with pytest.raises(auth_google.GoogleAuthError, match="not for this app"):
            auth_google.verify_id_token(_token(gis, aud="other"))

    def test_expired(self, gis):
        with pytest.raises(auth_google.GoogleAuthError, match="expired"):
            auth_google.verify_id_token(_token(gis, exp=int(time.time()) - 600))

    def test_bad_issuer(self, gis):
        with pytest.raises(auth_google.GoogleAuthError, match="issuer"):
            auth_google.verify_id_token(_token(gis, iss="https://evil.example"))

    def test_bad_signature(self, gis, keypair):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(auth_google.GoogleAuthError):
            auth_google.verify_id_token(_token(other))

    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        assert auth_google.enabled() is False
        with pytest.raises(auth_google.GoogleAuthError, match="not configured"):
            auth_google.verify_id_token("a.b.c")

    @pytest.mark.parametrize("bad", ["", "abc", "a.b", "x" * 5000])
    def test_garbage(self, gis, bad):
        with pytest.raises(auth_google.GoogleAuthError):
            auth_google.verify_id_token(bad)


class TestEndpoints:
    @pytest.fixture
    def app(self, gis, monkeypatch):
        from api.routers import google_auth as r

        db = {"bindings": {}, "users": {}, "lang": {}}

        def get_by_sub(sub):
            return db["bindings"].get(sub)

        def create_binding(sub, uid, email=None, name=None):
            db["bindings"] = {
                k: v for k, v in db["bindings"].items() if v["user_id"] != uid
            }
            db["bindings"][sub] = {
                "google_sub": sub,
                "user_id": uid,
                "email": email,
                "display_name": name,
            }
            return True

        def get_by_user(uid):
            return next(
                (v for v in db["bindings"].values() if v["user_id"] == uid), None
            )

        def delete_by_user(uid):
            before = len(db["bindings"])
            db["bindings"] = {
                k: v for k, v in db["bindings"].items() if v["user_id"] != uid
            }
            return len(db["bindings"]) != before

        monkeypatch.setattr(r, "get_binding_by_sub", get_by_sub)
        monkeypatch.setattr(r, "create_google_binding", create_binding)
        monkeypatch.setattr(r, "get_binding_by_user_id", get_by_user)
        monkeypatch.setattr(r, "delete_binding_by_user_id", delete_by_user)
        monkeypatch.setattr(r, "update_last_used", lambda sub: None)

        def create_or_get_user(identity, username=None, auth_method="ton_wallet"):
            new = identity not in db["users"]
            db["users"].setdefault(
                identity,
                {
                    "user_id": identity,
                    "username": username or identity,
                    "auth_method": auth_method,
                    "role": "user",
                    "membership_tier": "free",
                },
            )
            return {**db["users"][identity], "is_new": new}

        monkeypatch.setattr("core.database.user.create_or_get_user", create_or_get_user)
        monkeypatch.setattr(
            "core.database.user.set_user_language",
            lambda uid, lang: db["lang"].__setitem__(uid, lang) or True,
        )
        monkeypatch.setattr("api.routers.user._enforce_auth_lockout", lambda ip: None)
        monkeypatch.setattr("api.routers.user._clear_auth_failures", lambda ip: None)
        monkeypatch.setattr(
            "core.auth_failure_tracker.record_auth_failure", lambda ip, reason: None
        )

        app = FastAPI()
        app.include_router(r.router)
        app.state.db = db
        return app

    def test_login_creates_google_native_account_and_seeds_language(self, app, gis):
        c = TestClient(app)
        res = c.post("/api/user/google-login", json={"credential": _token(gis)})
        assert res.status_code == 200, res.text
        body = res.json()
        assert (
            body["user"]["user_id"] == "g_1089012345"
            and body["user"]["auth_method"] == "google"
        )
        assert body["user"]["has_wallet"] is False and body["is_new_user"] is True
        assert "access_token=" in "; ".join(res.headers.get_list("set-cookie"))
        assert app.state.db["lang"]["g_1089012345"] == "zh-TW"
        assert app.state.db["bindings"]["1089012345"]["user_id"] == "g_1089012345"

    def test_login_resolves_existing_binding(self, app, gis):
        app.state.db["bindings"]["1089012345"] = {
            "google_sub": "1089012345",
            "user_id": "0xabc",
            "email": None,
            "display_name": None,
        }
        app.state.db["users"]["0xabc"] = {
            "user_id": "0xabc",
            "username": "wallet",
            "auth_method": "evm_wallet",
            "role": "user",
            "membership_tier": "premium",
        }
        res = TestClient(app).post(
            "/api/user/google-login", json={"credential": _token(gis)}
        )
        assert res.status_code == 200
        assert (
            res.json()["user"]["user_id"] == "0xabc"
            and res.json()["user"]["has_wallet"] is True
        )
        assert (
            res.json()["is_new_user"] is False
            and "g_1089012345" not in app.state.db["users"]
        )

    def test_login_rejects_bad_token(self, app, gis):
        res = TestClient(app).post(
            "/api/user/google-login", json={"credential": _token(gis, aud="nope")}
        )
        assert res.status_code == 401

    def test_login_rejects_unverified_email(self, app, gis):
        res = TestClient(app).post(
            "/api/user/google-login",
            json={"credential": _token(gis, email_verified=False)},
        )
        assert res.status_code == 401

    def test_bind_status_unlink(self, app, gis):
        from api.deps import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {"user_id": "0xabc"}
        c = TestClient(app)
        assert c.get("/api/google/status").json() == {"bound": False, "enabled": True}
        assert (
            c.post("/api/google/bind", json={"credential": _token(gis)}).status_code
            == 200
        )
        st = c.get("/api/google/status").json()
        assert st["bound"] is True and st["email"] == "danny@example.com"
        # 同一個 Google 綁到別的帳號 → 409
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "0xdef"}
        assert (
            c.post("/api/google/bind", json={"credential": _token(gis)}).status_code
            == 409
        )
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "0xabc"}
        assert c.post("/api/google/unlink").json() == {"success": True, "bound": False}
        assert c.get("/api/google/status").json()["bound"] is False

    def test_unlink_refused_for_google_native_account(self, app):
        from api.deps import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {"user_id": "g_123"}
        assert TestClient(app).post("/api/google/unlink").status_code == 400

    def test_disabled_returns_503(self, app, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        assert (
            TestClient(app)
            .post("/api/user/google-login", json={"credential": "a.b.c" * 10})
            .status_code
            == 503
        )


class TestWiring:
    def test_config_exposes_client_id(self, monkeypatch):
        from api.routers import system

        monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
        app = FastAPI()
        app.include_router(system.router)
        assert (
            TestClient(app).get("/api/config").json()["google_client_id"] == CLIENT_ID
        )

    def test_csp_allows_gis(self):
        from api.middleware_setup import content_security_policy

        csp = content_security_policy()
        script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
        frame = next(d for d in csp.split(";") if d.strip().startswith("frame-src"))
        assert (
            "https://accounts.google.com" in script
            and "https://accounts.google.com" in frame
        )

    def test_frontend_files(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        assert (
            'id="google-login-container"' in html
            and 'id="google-login-wrap"' in html
            and "data-tma-hide" in html.split('id="google-login-wrap"')[1].split(">")[0]
        )
        main = (REPO / "web" / "js" / "main.js").read_text(encoding="utf-8")
        assert "import './google-auth.js';" in main
        ga = (REPO / "web" / "js" / "google-auth.js").read_text(encoding="utf-8")
        assert (
            "https://accounts.google.com/gsi/client" in ga
            and "/api/user/google-login" in ga
            and "/api/google/bind" in ga
        )
        conn = (REPO / "web" / "js" / "components" / "tab-connections.js").read_text(
            encoding="utf-8"
        )
        assert 'id="google-link-content"' in conn
        assert "GoogleLinkApp" in (REPO / "web" / "js" / "connections.js").read_text(
            encoding="utf-8"
        )
        assert "/api/google/status" in (
            REPO / "web" / "js" / "connections-settings.js"
        ).read_text(encoding="utf-8")
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            d = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert d["googleAuth"]["loginSuccess"] and d["googleAuth"]["linkHint"]

    def test_schema_and_migration(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert '("google_bindings", create_google_bindings_table)' in schema
        mig = (REPO / "alembic" / "versions" / "c051_google_bindings.py").read_text(
            encoding="utf-8"
        )
        assert 'revision = "c051"' in mig and 'down_revision = "c050"' in mig
        assert "user_id       TEXT NOT NULL UNIQUE" in mig
        assert "app.include_router(google_auth_router)" in (
            REPO / "api_server.py"
        ).read_text(encoding="utf-8")
