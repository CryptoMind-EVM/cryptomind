"""全面盤查（2026-09-25）認證批：refresh token 不能當 access token、bootstrap 只能提升自己、
「為我刪除」要是訊息參與者。"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import deps

pytestmark = pytest.mark.unit


def _client():
    app = FastAPI()

    @app.get("/me")
    async def me(user: dict = Depends(deps.get_current_user)):
        return {"user_id": user["user_id"]}

    @app.get("/me-id")
    async def me_id(user_id: str = Depends(deps.get_current_user_id)):
        return {"user_id": user_id}

    return TestClient(app)


@contextmanager
def _known_user():
    user = {"user_id": "0xabc", "is_active": True, "role": "user"}
    with patch.object(deps.user_repo, "get_by_id", new=AsyncMock(return_value=user)):
        yield


class TestRefreshTokenIsNotAnAccessToken:
    def test_access_token_still_works(self):
        token = deps.create_access_token({"sub": "0xabc"})
        with _known_user():
            res = _client().get("/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200 and res.json()["user_id"] == "0xabc"

    @pytest.mark.parametrize("path", ["/me", "/me-id"])
    def test_refresh_token_rejected(self, path):
        token = deps.create_refresh_token({"sub": "0xabc"})
        with _known_user():
            res = _client().get(path, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_verify_token_rejects_refresh(self):
        assert deps.verify_token(deps.create_access_token({"sub": "u"}))["sub"] == "u"
        with pytest.raises(HTTPException) as exc:
            deps.verify_token(deps.create_refresh_token({"sub": "u"}))
        assert exc.value.status_code == 401


class _Cur:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur = cur
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class TestBootstrapAdmin:
    def test_first_admin_can_only_promote_self(self):
        from api.routers.admin import users as mod

        cur = _Cur([(0,)])  # 目前沒有 admin
        conn = _Conn(cur)
        with patch.object(mod, "get_connection", return_value=conn):
            with pytest.raises(PermissionError):
                mod._bootstrap_admin_sync(
                    "0xvictim", {"user_id": "0xme", "role": "user"}
                )
        assert not conn.committed
        assert not any("UPDATE users" in sql for sql, _ in cur.executed)

    def test_first_admin_self_promotion_ok(self):
        from api.routers.admin import users as mod

        cur = _Cur([(0,)])
        conn = _Conn(cur)
        with patch.object(mod, "get_connection", return_value=conn):
            assert mod._bootstrap_admin_sync(
                "0xme", {"user_id": "0xme", "role": "user"}
            )
        assert conn.committed


class TestHideMessageRequiresParticipant:
    def test_non_participant_gets_not_found(self):
        from core.database.messages import helpers

        cur = _Cur([])  # 參與者條件下查無此訊息
        with patch.object(helpers, "get_connection", return_value=_Conn(cur)):
            res = helpers.hide_dm_message_for_user(42, "0xstranger")
        assert res == {"success": False, "error": "message_not_found"}
        sql, params = cur.executed[0]
        assert "from_user_id = %s OR to_user_id = %s" in sql
        assert params == (42, "0xstranger", "0xstranger")
        assert len(cur.executed) == 1, "不是參與者就不能寫 dm_message_deletions"


class TestAnalyzeSessionOwnership:
    """/api/analyze 的 session_id 是前端帶的：存在但屬於別人要 404（以前能讀別人的歷史）。"""

    def _client(self):
        from api.routers import analysis as mod

        app = FastAPI()
        app.include_router(mod.router)

        async def fake_user():
            return {"user_id": "0xme", "membership_tier": "free"}

        async def fake_session():
            yield None

        app.dependency_overrides[mod.get_current_user] = fake_user
        app.dependency_overrides[mod.get_async_session] = fake_session
        return mod, TestClient(app)

    @pytest.mark.parametrize(
        "owner, status", [("0xother", 404), ("0xme", 418), (None, 418)]
    )
    def test_foreign_session_rejected(self, owner, status):
        mod, client = self._client()

        async def past_the_check(*a, **k):  # 走過擁有者檢查就停，不跑整條分析
            raise HTTPException(status_code=418)

        with (
            patch.object(mod, "get_session_owner", return_value=owner),
            patch.object(mod, "_load_resume_model_context", side_effect=past_the_check),
        ):
            res = client.post(
                "/api/analyze", json={"message": "hi", "session_id": "s-123"}
            )
        assert res.status_code == status


def test_refresh_endpoint_still_accepts_refresh_token():
    """/api/user/refresh 自己要解 refresh token：只有它能 allow_refresh。"""
    refresh = deps.create_refresh_token({"sub": "u"})
    assert deps.verify_token(refresh, allow_refresh=True)["type"] == "refresh"
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "api/routers/user.py").read_text(
        encoding="utf-8"
    )
    assert "verify_token(refresh_token_value, allow_refresh=True)" in src
    assert src.count("allow_refresh=True") == 1, (
        "只有 refresh 端點可以放行 refresh token"
    )
