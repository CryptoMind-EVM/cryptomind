"""POST /api/chat/sessions 失敗處理測試。

驗證兩個層面：

1. **create_session DB 函式** 失敗時必須 re-raise（過去 except 吞掉例外，
   endpoint 收不到錯誤 → 回 200 + 假的 session_id → 前端後續訊息找不到 session）。

2. **POST /api/chat/sessions endpoint** 接到 RuntimeError（DB init 進行中）
   要回 **503 + ``database_initializing``**，讓前端知道稍候再試。

標記 integration（需要 FastAPI app + DB mock）。
"""

from __future__ import annotations

import pytest

# ============================================================================
# create_session 函式層（unit test，純 Python）
# ============================================================================


def test_create_session_reraises_on_db_failure(monkeypatch):
    """create_session 在 INSERT 失敗時必須 re-raise，不可吞掉。

    過去 except 只 log + rollback 沒 raise，導致 endpoint 收到 None 仍回 200。
    """
    from core.database import chat as chat_mod

    # Fake 一個會拋例外的 connection
    class _FakeCursor:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated DB write failure")

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(chat_mod, "get_connection", lambda: _FakeConn())

    with pytest.raises(RuntimeError, match="simulated DB write failure"):
        chat_mod.create_session("test-sid", title="test", user_id="u1")


def test_create_session_reraises_on_connection_failure(monkeypatch):
    """get_connection 本身失敗（pool 滿 / DB 暫斷）也要 re-raise。

    過去 conn = get_connection() 在 try 外，pool 滿時直接冒 500。
    """
    from core.database import chat as chat_mod

    def _broken_get_connection():
        raise ConnectionError("pool exhausted")

    monkeypatch.setattr(chat_mod, "get_connection", _broken_get_connection)

    with pytest.raises(ConnectionError, match="pool exhausted"):
        chat_mod.create_session("test-sid", title="test", user_id="u1")


# ============================================================================
# Endpoint 層（integration，FastAPI app）
# ============================================================================


@pytest.mark.integration
class TestCreateSessionEndpoint:
    @pytest.mark.asyncio
    async def test_503_when_db_initializing(self, client, auth_headers):
        """DB init 進行中 → 503 + database_initializing，前端可顯示「稍候再試」。"""
        from unittest.mock import patch

        with patch("api.routers.analysis.run_sync") as mock_run:

            async def _raise_init_error(fn, *args):
                raise RuntimeError("Database initialization is still in progress")

            mock_run.side_effect = _raise_init_error

            response = await client.post(
                "/api/chat/sessions",
                json={"title": "test"},
                headers=auth_headers,
            )

        assert response.status_code == 503
        assert response.json()["detail"] == "database_initializing"

    @pytest.mark.asyncio
    async def test_503_on_generic_db_failure(self, client, auth_headers):
        """其他 DB 錯誤（pool 滿 / write 失敗）→ 503 + database_unavailable。"""
        from unittest.mock import patch

        with patch("api.routers.analysis.run_sync") as mock_run:

            async def _raise_generic(fn, *args):
                raise RuntimeError("pool exhausted")

            mock_run.side_effect = _raise_generic

            response = await client.post(
                "/api/chat/sessions",
                json={"title": "test"},
                headers=auth_headers,
            )

        assert response.status_code == 503
        assert response.json()["detail"] == "database_unavailable"

    @pytest.mark.asyncio
    async def test_success_returns_session_id(self, client, auth_headers):
        """正常情境仍回 200 + session_id（regression）。"""
        from unittest.mock import patch

        with patch("api.routers.analysis.run_sync") as mock_run:
            mock_run.return_value = None  # create_session 成功（無回傳值）

            response = await client.post(
                "/api/chat/sessions",
                json={"title": "test"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "session_id" in body
        assert body["title"] == "test"
