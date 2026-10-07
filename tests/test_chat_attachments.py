"""對話附圖存儲測試（vision Phase 2，docs/plans/2026-08-30-vision-image-storage-design.md）.

涵蓋設計文件測試清單：
- 存取往返與 owner-only（他人/不存在一律 404，不洩漏存在性）
- Cache-Control: private（內容含使用者資料，僅私有快取）
- flag off（VISION_STORAGE_ENABLED=false）不寫入
- decode 再驗（大小上限 / 格式白名單）
- 30 天保留清理 purge_expired（直呼函式）
- 歷史 API 帶 attachment_id：metadata 欄位為既有回傳行為
  （core/database/chat.py:get_chat_history 既有 json.loads），本檔聚焦新元件。
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.orm.chat_attachments_repo import chat_attachments_repo

OWNER = {"user_id": "u_owner", "username": "owner"}
OTHER = {"user_id": "u_other", "username": "other"}

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-bytes").decode()
_DATA_URL = f"data:image/png;base64,{_PNG_B64}"


def _make_app(user: dict):
    from api.routers import attachments as att_module

    app = FastAPI()
    app.include_router(att_module.router)

    async def fake_user():
        return user

    async def fake_session():
        yield None  # repo 被 mock，session 不會被用到

    app.dependency_overrides[att_module.get_current_user] = fake_user
    app.dependency_overrides[att_module.get_async_session] = fake_session
    return app


class TestGetAttachmentEndpoint:
    def test_owner_roundtrip_200_with_private_cache(self):
        """存取往返：owner 取回自己的圖（bytes＋mime＋private cache）。"""
        app = _make_app(OWNER)
        owned = SimpleNamespace(
            id=42, user_id="u_owner", mime="jpeg", data=b"jpeg-bytes"
        )
        with patch.object(
            chat_attachments_repo, "get_owned", AsyncMock(return_value=owned)
        ) as mock_get:
            client = TestClient(app)
            resp = client.get("/api/attachments/42")
            assert resp.status_code == 200
            assert resp.content == b"jpeg-bytes"
            assert resp.headers["content-type"] == "image/jpeg"
            assert resp.headers["cache-control"] == "private, max-age=86400"
            mock_get.assert_awaited_once()

    def test_not_owner_returns_404_not_403(self):
        """owner-only：他人或不存在一律 404——不洩漏附件存在性（防 IDOR）。"""
        app = _make_app(OTHER)
        with patch.object(
            chat_attachments_repo, "get_owned", AsyncMock(return_value=None)
        ):
            client = TestClient(app)
            resp = client.get("/api/attachments/42")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "ATTACHMENT_NOT_FOUND"

    def test_invalid_id_returns_422(self):
        app = _make_app(OWNER)
        client = TestClient(app)
        resp = client.get("/api/attachments/not-a-number")
        assert resp.status_code == 422


class TestStorageFlag:
    def test_flag_off_returns_none_without_db_touch(self):
        """回滾：VISION_STORAGE_ENABLED=false 停寫入（讀取保留）。"""
        from api.vision import store_image_attachment

        with patch(
            "api.vision.image_storage_enabled", return_value=False
        ), patch("core.orm.session.using_session") as mock_sess:
            result = asyncio.run(store_image_attachment(_DATA_URL, "u1", "s1"))
            assert result is None
            mock_sess.assert_not_called()

    def test_flag_on_stores_and_returns_id(self):
        from api.vision import store_image_attachment

        created = SimpleNamespace(id=7)

        async def _fake_create(db, **kwargs):
            assert kwargs["mime"] == "png"
            assert kwargs["data"] == b"\x89PNG\r\n\x1a\nfake-bytes"
            return created

        class _NullSession:
            async def __aenter__(self):
                return AsyncMock()

            async def __aexit__(self, *exc):
                return False

        with patch(
            "api.vision.image_storage_enabled", return_value=True
        ), patch.object(
            chat_attachments_repo, "create", AsyncMock(side_effect=_fake_create)
        ), patch("core.orm.session.using_session", return_value=_NullSession()):
            result = asyncio.run(store_image_attachment(_DATA_URL, "u1", "s1"))
            assert result == 7


class TestDecode:
    def test_decodes_valid_data_url(self):
        from api.vision import decode_image_data_url

        raw, mime = decode_image_data_url(_DATA_URL)
        assert raw == b"\x89PNG\r\n\x1a\nfake-bytes"
        assert mime == "png"

    def test_rejects_invalid_format(self):
        from api.vision import VisionError, decode_image_data_url

        with pytest.raises(VisionError):
            decode_image_data_url("data:text/html;base64,AAAA")

    def test_rejects_oversize(self):
        from api.vision import VisionError, decode_image_data_url

        big = base64.b64encode(b"x" * (5 * 1024 * 1024)).decode()
        with pytest.raises(VisionError):
            decode_image_data_url(f"data:image/png;base64,{big}")


class TestPurgeExpired:
    def test_purge_returns_rowcount(self):
        """保留清理（直呼函式）：回傳刪除列數，單一刪除語句。"""
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(rowcount=3)

        deleted = asyncio.run(chat_attachments_repo.purge_expired(db))
        assert deleted == 3
        db.execute.assert_awaited_once()

    def test_purge_none_rowcount_is_zero(self):
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(rowcount=None)

        assert asyncio.run(chat_attachments_repo.purge_expired(db)) == 0
