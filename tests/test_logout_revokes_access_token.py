"""登出要讓 access token 當場失效。

之前 logout 只撤銷 refresh token：複製走的 access token 在 exp 前（最長 24h）照樣能用。
denylist 沿用 api.deps 既有的 token 黑名單：Redis 優先（跨 worker 共享），連不上退回
in-process 記憶體。key：新 token 用 jti，舊 token（沒有 jti）用整顆 token 的 sha256。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import deps

USER = {
    "user_id": "logout-revoke-test-user",  # 別跟其他測試共用：/api/user/me 有 _ME_CACHE
    "username": "LogoutRevokeTest",
    "role": "user",
    "is_active": True,
}


@pytest.fixture(autouse=True)
def _isolated_denylist(tmp_path, monkeypatch):
    """每個測試一份乾淨黑名單；預設沒有 Redis（走 in-process fallback），檔案寫到 tmp。"""
    monkeypatch.setattr(deps, "_REVOKED_TOKENS_FILE", tmp_path / "revoked_tokens.json")
    monkeypatch.setattr(deps, "_get_revoked_redis_client", lambda: None)
    with deps._revoked_tokens_lock:
        deps._revoked_tokens.clear()
    yield
    with deps._revoked_tokens_lock:
        deps._revoked_tokens.clear()


class _FakeRedis:
    """跨 worker 共用的 Redis：只需要 set(ex=) 與 exists。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttl[key] = ex

    def exists(self, key):
        return int(key in self.store)


class _BrokenRedis:
    def set(self, *a, **k):
        raise ConnectionError("redis down")

    def exists(self, *a, **k):
        raise ConnectionError("redis down")


@contextmanager
def _me_endpoint_mocks():
    with (
        patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=USER)),
        patch("api.routers.user.user_repo.get_by_id", new=AsyncMock(return_value=USER)),
        patch(
            "api.routers.user.user_repo.get_language", new=AsyncMock(return_value=None)
        ),
        patch(
            "api.routers.user.user_repo.get_display_name",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routers.user.user_llm_preferences_repo.get_selected_provider",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_access_token(**kwargs) -> str:
    return deps.create_access_token(
        {"sub": USER["user_id"], "username": USER["username"]}, **kwargs
    )


def _legacy_access_token() -> str:
    """這次之前簽出的 access token：沒有 jti。"""
    return jwt.encode(
        {"sub": USER["user_id"], "type": "access", "exp": int(time.time()) + 3600},
        deps.SECRET_KEY,
        algorithm=deps.ALGORITHM,
    )


def _deps_client() -> TestClient:
    app = FastAPI()

    @app.get("/me")
    async def me(user: dict = Depends(deps.get_current_user)):
        return {"user_id": user["user_id"]}

    @app.get("/me-id")
    async def me_id(user_id: str = Depends(deps.get_current_user_id)):
        return {"user_id": user_id}

    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# 端點：登出 → 同一顆 access token 401；別的 session、refresh 不受影響
# ──────────────────────────────────────────────────────────────────────────────


class TestLogoutEndpoint:
    async def test_token_rejected_after_logout(self, client):
        token = _new_access_token()
        with _me_endpoint_mocks():
            before = await client.get("/api/user/me", headers=_bearer(token))
            assert before.status_code == 200

            res = await client.post("/api/user/logout", headers=_bearer(token))
            assert res.status_code == 200

            after = await client.get("/api/user/me", headers=_bearer(token))
            assert after.status_code == 401

    async def test_other_session_token_still_valid(self, client):
        mine = _new_access_token()
        other = _new_access_token()
        # 同一秒、同一人簽的兩顆也要分得開（靠 jti），否則登出一個會連帶踢掉另一個
        assert mine != other
        with _me_endpoint_mocks():
            await client.post("/api/user/logout", headers=_bearer(mine))
            res = await client.get("/api/user/me", headers=_bearer(other))
        assert res.status_code == 200

    async def test_refresh_still_works_after_another_session_logs_out(self, client):
        await client.post("/api/user/logout", headers=_bearer(_new_access_token()))

        refresh = deps.create_refresh_token(
            {"sub": USER["user_id"], "username": USER["username"]}
        )
        client.cookies.set("refresh_token", refresh)
        with _me_endpoint_mocks():
            res = await client.post("/api/user/refresh")
            assert res.status_code == 200
            new_access = res.cookies["access_token"]
            client.cookies.clear()
            me = await client.get("/api/user/me", headers=_bearer(new_access))
        assert me.status_code == 200

    async def test_logout_revokes_both_access_and_refresh(self, client):
        access = _new_access_token()
        refresh = deps.create_refresh_token(
            {"sub": USER["user_id"], "username": USER["username"]}
        )
        client.cookies.set("refresh_token", refresh)
        with _me_endpoint_mocks():
            await client.post("/api/user/logout", headers=_bearer(access))
            client.cookies.clear()
            me = await client.get("/api/user/me", headers=_bearer(access))
            client.cookies.set("refresh_token", refresh)
            ref = await client.post("/api/user/refresh")
        assert me.status_code == 401
        assert ref.status_code == 401

    @pytest.mark.parametrize("kind", ["missing", "garbage", "expired"])
    async def test_logout_succeeds_without_a_usable_token(self, client, kind):
        headers = {
            "missing": {},
            "garbage": _bearer("not-a-jwt"),
            "expired": _bearer(_new_access_token(expires_delta=timedelta(seconds=-5))),
        }[kind]
        res = await client.post("/api/user/logout", headers=headers)
        assert res.status_code == 200
        assert res.json() == {"success": True}
        assert deps._revoked_tokens == {}  # 過期／偽造的 token 不寫進 denylist


# ──────────────────────────────────────────────────────────────────────────────
# denylist：key、TTL、Redis／fallback
# ──────────────────────────────────────────────────────────────────────────────


class TestAccessTokenDenylist:
    def test_access_token_has_unique_jti(self):
        a = jwt.decode(
            _new_access_token(), deps.SECRET_KEY, algorithms=[deps.ALGORITHM]
        )
        b = jwt.decode(
            _new_access_token(), deps.SECRET_KEY, algorithms=[deps.ALGORITHM]
        )
        assert a["jti"] and b["jti"] and a["jti"] != b["jti"]

    @pytest.mark.parametrize("path", ["/me", "/me-id"])
    def test_legacy_token_without_jti_can_be_revoked(self, path):
        legacy = _legacy_access_token()
        with patch.object(
            deps.user_repo, "get_by_id", new=AsyncMock(return_value=USER)
        ):
            client = _deps_client()
            assert client.get(path, headers=_bearer(legacy)).status_code == 200
            deps.revoke_access_token(legacy)
            assert client.get(path, headers=_bearer(legacy)).status_code == 401
        assert deps._hash_token(legacy) in deps._revoked_tokens

        with pytest.raises(HTTPException) as exc:
            deps.verify_token(legacy)
        assert exc.value.status_code == 401

    def test_ttl_is_remaining_lifetime_from_exp(self):
        fake = _FakeRedis()
        token = _new_access_token(expires_delta=timedelta(minutes=10))
        payload = jwt.decode(token, deps.SECRET_KEY, algorithms=[deps.ALGORITHM])
        with patch.object(deps, "_get_revoked_redis_client", return_value=fake):
            deps.revoke_access_token(token)

        key = deps._REDIS_KEY_PREFIX + "jti:" + payload["jti"]
        assert key in fake.store
        assert abs(fake.ttl[key] - (payload["exp"] - time.time())) <= 2
        assert 590 <= fake.ttl[key] <= 600

    def test_revocation_visible_to_another_worker_via_redis(self):
        fake = _FakeRedis()
        token = _new_access_token()
        with patch.object(deps, "_get_revoked_redis_client", return_value=fake):
            deps.revoke_access_token(token)
            assert deps._revoked_tokens == {}  # 走 Redis，沒落到本機記憶體
            with pytest.raises(HTTPException):
                deps.verify_token(token)  # 別的 worker 只看得到 Redis

    def test_redis_down_falls_back_to_memory(self):
        token = _new_access_token()
        with patch.object(
            deps, "_get_revoked_redis_client", return_value=_BrokenRedis()
        ):
            deps.revoke_access_token(token)  # 不拋錯
            with pytest.raises(HTTPException):
                deps.verify_token(token)

    def test_refresh_token_still_verifies_with_allow_refresh(self):
        refresh = deps.create_refresh_token({"sub": "u1"})
        assert deps.verify_token(refresh, allow_refresh=True)["sub"] == "u1"
        # 當 access token 送去登出不會寫進 access denylist
        deps.revoke_access_token(refresh)
        assert deps._revoked_tokens == {}
        assert deps.verify_token(refresh, allow_refresh=True)["sub"] == "u1"


def test_revoked_refresh_token_is_rejected_by_verify_token():
    """review：refresh token 也帶 jti，但 revoke_token 寫的是 sha256 key；
    denylist key 要跟著 token 類型走，不然 verify_token(allow_refresh=True) 查不到。"""
    refresh = deps.create_refresh_token({"sub": "u1"})
    deps.revoke_token(refresh)
    with pytest.raises(HTTPException):
        deps.verify_token(refresh, allow_refresh=True)
