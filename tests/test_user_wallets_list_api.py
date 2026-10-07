"""GET /api/user/wallets（2026-09-11 盤查）：付款前綁定檢查的資料來源。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

USER = {"user_id": "tg_123", "username": "tg", "membership_tier": "free"}


def _client():
    from api.routers import user as user_module

    app = FastAPI()
    app.include_router(user_module.router)

    async def fake_user():
        return USER

    app.dependency_overrides[user_module.get_current_user] = fake_user
    return TestClient(app), user_module


def test_lists_only_evm_addresses_for_payment():
    client, user_module = _client()
    rows = [
        {
            "chain": "evm",
            "address": "0xAbC0000000000000000000000000000000000001",
            "is_primary": True,
        },
        {"chain": "ton", "address": "UQabc", "is_primary": False},
        {"chain": "evm", "address": None},
    ]
    with patch.object(
        user_module.user_wallet_repo, "list_for_user", AsyncMock(return_value=rows)
    ):
        resp = client.get("/api/user/wallets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["evm_addresses"] == ["0xAbC0000000000000000000000000000000000001"]
    assert len(body["wallets"]) == 3


def test_repo_failure_degrades_to_empty_list():
    client, user_module = _client()
    with patch.object(
        user_module.user_wallet_repo,
        "list_for_user",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        resp = client.get("/api/user/wallets")
    assert resp.status_code == 200
    assert resp.json()["evm_addresses"] == []
