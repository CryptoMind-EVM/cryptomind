"""admin 手動核銷 USDC（2026-09-12）：自動驗證對不上的付款由 admin 認定歸屬。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

ADMIN = {"user_id": "admin-1", "username": "admin", "role": "admin"}
TX = "0x" + "ab" * 32


def _client():
    from api.routers.admin import users as users_module

    app = FastAPI()
    app.include_router(users_module.router, prefix="/api/admin")

    async def fake_admin():
        return ADMIN

    app.dependency_overrides[users_module.require_admin] = fake_admin
    return TestClient(app)


def _receipt(micro):
    return {
        "tx_hash": TX,
        "sender": "0x" + "11" * 20,
        "amount_micro": micro,
        "block": 1,
        "confirmations": 20,
    }


def test_settles_when_transfer_covers_plan_price():
    client = _client()
    with (
        patch(
            "api.payment_rails.inspect_evm_usdc_receipt",
            AsyncMock(return_value=_receipt(12_000_473)),
        ),
        patch(
            "core.config.PREMIUM_USD_PRICES",
            {"premium_monthly": 12.0, "premium_yearly": 108.0},
        ),
        patch("core.database.user.upgrade_to_pro", return_value=True) as up,
    ):
        resp = client.post(
            "/api/admin/users/u-7/settle-usdc",
            json={"tx_hash": TX, "plan": "premium_monthly"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True and body["months"] == 1
    kwargs = up.call_args.kwargs
    assert kwargs == {
        "user_id": "u-7",
        "months": 1,
        "tx_hash": TX,
        "amount": 12.000473,
    }, "tx_hash 要進 membership_payments 擋重複核銷；amount 記鏈上實收 USDC"


def test_rejects_transfer_below_plan_price():
    client = _client()
    with (
        patch(
            "api.payment_rails.inspect_evm_usdc_receipt",
            AsyncMock(return_value=_receipt(5_000_000)),
        ),
        patch(
            "core.config.PREMIUM_USD_PRICES",
            {"premium_monthly": 12.0, "premium_yearly": 108.0},
        ),
        patch("core.database.user.upgrade_to_pro") as up,
    ):
        resp = client.post(
            "/api/admin/users/u-7/settle-usdc",
            json={"tx_hash": TX, "plan": "premium_monthly"},
        )
    assert resp.status_code == 400
    up.assert_not_called()


def test_duplicate_settle_is_409():
    client = _client()
    with (
        patch(
            "api.payment_rails.inspect_evm_usdc_receipt",
            AsyncMock(return_value=_receipt(12_000_000)),
        ),
        patch(
            "core.config.PREMIUM_USD_PRICES",
            {"premium_monthly": 12.0, "premium_yearly": 108.0},
        ),
        patch(
            "core.database.user.upgrade_to_pro",
            side_effect=ValueError("此交易已被處理（transaction hash已存在）"),
        ),
    ):
        resp = client.post("/api/admin/users/u-7/settle-usdc", json={"tx_hash": TX})
    assert resp.status_code == 409


def test_receipt_errors_propagate():
    client = _client()
    with patch(
        "api.payment_rails.inspect_evm_usdc_receipt",
        AsyncMock(
            side_effect=HTTPException(
                status_code=400,
                detail="No USDC transfer to the receiving address found in this transaction",
            )
        ),
    ):
        resp = client.post("/api/admin/users/u-7/settle-usdc", json={"tx_hash": TX})
    assert resp.status_code == 400
    assert "No USDC transfer" in resp.json()["detail"]


def test_bad_tx_hash_is_422():
    client = _client()
    resp = client.post("/api/admin/users/u-7/settle-usdc", json={"tx_hash": "0x123"})
    assert resp.status_code == 422
