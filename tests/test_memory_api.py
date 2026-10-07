"""Memory Management API 測試。

測 /api/memory/facts 的 GET/PATCH/DELETE——讓使用者自行管理記憶。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """建一個最小 FastAPI app 只含 memory router。"""
    from fastapi import FastAPI

    from api.deps import get_current_user
    from api.routers.memory import router

    app = FastAPI()
    app.include_router(router)

    # mock auth——回傳固定 user
    async def fake_user():
        return {"user_id": "test_user_123", "username": "tester"}

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


@pytest.fixture
def mock_store():
    """mock MemoryStore，不碰真實 DB。"""
    store = MagicMock()
    store.read_facts.return_value = {
        "preference_abc": {"value": "偏好技術面分析", "category": "preference", "confidence": "high"},
        "holding_xyz": {"value": "持有 2.3 BTC", "category": "holding", "confidence": "high"},
    }
    store.write_facts.return_value = None
    store.delete_fact.return_value = True
    with patch("api.routers.memory._get_store", return_value=store):
        yield store


class TestListFacts:
    def test_list_returns_all_facts(self, client, mock_store):
        """GET /api/memory/facts 列出所有記憶。"""
        resp = client.get("/api/memory/facts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["facts"]) == 2
        categories = [f["category"] for f in data["facts"]]
        assert "preference" in categories
        assert "holding" in categories


class TestUpdateFact:
    def test_update_modifies_value(self, client, mock_store):
        """PATCH /api/memory/facts/{key} 修改記憶內容。"""
        resp = client.patch(
            "/api/memory/facts/preference_abc",
            json={"value": "偏好基本面分析", "category": "preference"},
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "偏好基本面分析"
        # 確認 write_facts 被呼叫
        mock_store.write_facts.assert_called_once()

    def test_update_normalizes_category(self, client, mock_store):
        """未知 category 正規化成 fact。"""
        resp = client.patch(
            "/api/memory/facts/some_key",
            json={"value": "測試", "category": "unknown_type"},
        )
        assert resp.status_code == 200
        assert resp.json()["category"] == "fact"


class TestDeleteFact:
    def test_delete_removes_fact(self, client, mock_store):
        """DELETE /api/memory/facts/{key} 刪除記憶。"""
        resp = client.delete("/api/memory/facts/holding_xyz")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_store.delete_fact.assert_called_once_with("holding_xyz")
