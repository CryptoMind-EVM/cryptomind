"""Premium API entitlement / 報價 metadata 測試（design 2026-08-13 §9.4 / §6.2）。

- /pricing：回傳月／年方案（USD）。premium 的 TON 報價 metadata 2026-09-25 移除。
- /status：回傳 entitlement（與 cron / wallet_monitor 同一來源）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.entitlement import resolve_entitlement

PREMIUM = resolve_entitlement(tier="premium", is_expired=False)
FREE = resolve_entitlement(tier="free")


def _mocked_user(uid: str) -> dict:
    return {
        "user_id": uid, "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }


# --------------------------------------------------------------------------- #
# /pricing 報價 metadata（公開、無 auth）                                       #
# --------------------------------------------------------------------------- #
class TestPricingPlans:
    def test_pricing_includes_monthly_and_yearly(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import api.routers.premium as pm

        app = FastAPI()
        app.include_router(pm.router)
        client = TestClient(app)

        resp = client.get("/api/premium/pricing")
        assert resp.status_code == 200
        body = resp.json()
        # 月／年方案都回（年繳後端早已支援）
        assert set(body["pricing"]["premium"]) == {"monthly", "yearly"}
        assert set(body["rails"][0]["amounts"]) == {"premium_monthly", "premium_yearly"}
        # premium 的 TON 報價已移除；論壇價 2026-09-25 起在 forum 區塊（USD）
        assert "ton" not in body
        assert "quote_expiry_minutes" not in body["forum"]


# --------------------------------------------------------------------------- #
# /status entitlement                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_status_returns_premium_entitlement(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQprem"})
    membership = {
        "is_premium": True,
        "membership_tier": "premium",
        "days_remaining": 30,
        "membership_expires_at": "2026-12-13T00:00:00+00:00",
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQprem"))), \
         patch("core.orm.repositories.user_repo.get_membership", new=AsyncMock(return_value=membership)), \
         patch("api.routers.premium.resolve_entitlement_for_user", return_value=PREMIUM):
        resp = await client.get(
            "/api/premium/status", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["membership"]["membership_tier"] == "premium"
    assert payload["entitlement"]["is_premium"] is True
    assert payload["entitlement"]["can_use_scheduled_monitoring"] is True


@pytest.mark.asyncio
async def test_status_returns_free_entitlement(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQfree"})
    membership = {
        "is_premium": False,
        "membership_tier": "free",
        "days_remaining": 0,
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQfree"))), \
         patch("core.orm.repositories.user_repo.get_membership", new=AsyncMock(return_value=membership)), \
         patch("api.routers.premium.resolve_entitlement_for_user", return_value=FREE):
        resp = await client.get(
            "/api/premium/status", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["entitlement"]["tier"] == "free"
    assert payload["entitlement"]["can_use_scheduled_monitoring"] is False
