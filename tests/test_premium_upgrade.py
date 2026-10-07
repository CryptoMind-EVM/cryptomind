"""Tests for premium membership upgrade endpoint."""

from unittest.mock import patch

import pytest
from slowapi.errors import RateLimitExceeded

from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from api.routers.premium import UpgradeRequest


def _create_test_app():
    """Create a FastAPI test app with limiter configured."""
    from fastapi import FastAPI

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    return app


@pytest.fixture(autouse=True)
def _set_test_mode_false(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "false")


def _make_app_with_deps(premium_router):
    """Create a test FastAPI app with mocked auth dependency."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(premium_router)

    app.dependency_overrides[premium_router.dependencies[0].dependency] = lambda: {
        "user_id": "test-user-1"
    }

    return app, TestClient(app)


class TestUpgradeEndpoint:
    """Tests for the /upgrade endpoint logic (inspect-based, no TestClient)."""

    def test_upgrade_endpoint_requires_ton_order_in_production(self):
        import inspect

        import api.routers.premium as pm

        source = inspect.getsource(pm.upgrade_to_premium)
        assert "order_token" in source
        assert "comment" in source
        assert "TEST_MODE" in source
        assert "not TEST_MODE" in source or "if not TEST_MODE" in source

    def test_upgrade_endpoint_validates_plan(self):
        import inspect

        import api.routers.premium as pm

        source = inspect.getsource(pm.upgrade_to_premium)
        assert "Invalid plan" in source
        assert "PLAN_MONTHS" in source


class TestPricingEndpoint:
    """Tests for the /pricing endpoint."""

    def test_returns_pricing_data(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import api.routers.premium as pm

        app = FastAPI()
        app.include_router(pm.router)

        client = TestClient(app)

        # USD 訂閱價（monthly 12 / yearly 108）；premium 的 TON 換算 2026-09-25 移除。
        with patch(
            "api.routers.premium.PREMIUM_USD_PRICES",
            {"premium_monthly": 12.0, "premium_yearly": 108.0},
        ):
            response = client.get("/api/premium/pricing")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "premium" in data["pricing"]
        assert data["pricing"]["premium"]["monthly"] == 12.0
        assert data["pricing"]["premium"]["yearly"] == 108.0
        # 論壇價 2026-09-25 起在中性的 forum 區塊（USD）；ton 區塊移除
        assert "ton" not in data
        assert "premium_monthly" not in data["forum"]["prices"]


class TestUpgradeRequestModel:
    """Tests for UpgradeRequest model."""

    def test_ton_order_fields_optional(self):
        req = UpgradeRequest(
            plan="premium_monthly",
            tx_hash="tx_abc",
        )
        assert req.order_token is None
        assert req.comment is None
        assert req.tx_hash == "tx_abc"

    def test_ton_order_fields_can_be_set(self):
        req = UpgradeRequest(
            plan="premium_yearly",
            order_token="tok.abc",
            comment="cm123",
            tx_hash="tx_abc",
        )
        assert req.order_token == "tok.abc"
        assert req.comment == "cm123"
        assert req.tx_hash == "tx_abc"

    def test_default_plan_is_monthly(self):
        req = UpgradeRequest()
        assert req.plan == "premium_monthly"
        assert req.months is None

    # months 以前收下就丟：送 months=3 照樣只給 1 個月、不報錯。
    # 期數由 plan 決定（PLAN_MONTHS），months 只准等於該方案的期數或不帶。

    @pytest.mark.parametrize(
        "plan,months", [("premium_monthly", 1), ("premium_yearly", 12)]
    )
    def test_months_matching_plan_accepted(self, plan, months):
        assert UpgradeRequest(plan=plan, months=months).months == months

    @pytest.mark.parametrize(
        "plan,months",
        [("premium_monthly", 3), ("premium_monthly", 12), ("premium_yearly", 1)],
    )
    def test_months_not_matching_plan_rejected(self, plan, months):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UpgradeRequest(plan=plan, months=months)

    def test_upgrade_endpoint_returns_422_for_mismatched_months(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import api.routers.premium as pm
        from api.deps import get_current_user

        app = FastAPI()
        app.include_router(pm.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
        resp = TestClient(app).post(
            "/api/premium/upgrade", json={"plan": "premium_monthly", "months": 3}
        )
        assert resp.status_code == 422
        assert "months" in resp.text

    def test_frontend_months_follow_plan_months(self):
        """premium.js 送的 months 要跟 PLAN_MONTHS 一致，否則年繳 claim 全被 422。"""
        from pathlib import Path

        import api.routers.premium as pm

        js = (Path(__file__).resolve().parents[1] / "web/js/premium.js").read_text(
            encoding="utf-8"
        )
        assert "months: plan === 'premium_yearly' ? 12 : 1," in js
        assert pm.PLAN_MONTHS == {"premium_monthly": 1, "premium_yearly": 12}


class TestSubscriptionRailUnification:
    """2026-09-09 訂閱統一 USDC on Base。

    設計文件：docs/plans/2026-09-09-premium-payment-unify-evm-usdc-design.md
    ——TON 兩軌不再收單、/ton-order 退場（410）、claim（/upgrade）不動。
    """

    def _client(self):
        """premium router 的 auth 是逐路由 Depends(get_current_user)——
        直接覆寫該 dependency（_make_app_with_deps 假設 router 級
        dependencies，不適用）。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import api.routers.premium as pm
        from api.deps import get_current_user

        app = FastAPI()
        app.include_router(pm.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user-1"}
        return TestClient(app)

    def test_payment_order_rejects_ton_native(self):
        """ton_native 2026-09-25 從 Literal 移除：舊客戶端 → 422，不是 500。"""
        client = self._client()
        resp = client.post(
            "/api/premium/payment-order",
            json={"plan": "premium_monthly", "rail": "ton_native"},
        )
        assert resp.status_code == 422, "ton_native 不得再收單"

    def test_payment_order_ton_usdt_rail_removed(self):
        """TON USD₮ 軌 2026-09-25 整條移除：舊客戶端還帶這個值 → 422（驗證錯誤），不是 500。"""
        client = self._client()
        resp = client.post(
            "/api/premium/payment-order",
            json={"plan": "premium_monthly", "rail": "ton_usdt"},
        )
        assert resp.status_code == 422

    def test_claim_ton_usdt_order_token_is_unknown_rail(self, monkeypatch):
        """已簽發的 TON USD₮ 訂單（7 天 TTL，理論上都過期了）拿來 claim →
        400 Unknown order rail，而且不會去打鏈上 API。"""
        import api.payment_rails as pr
        import api.routers.premium as pm
        from api.evm_verification import create_multichain_order

        async def _no_bindings(user_id, chain):
            return []

        def _no_network(*args, **kwargs):
            raise AssertionError("removed rail must not reach the network")

        monkeypatch.setattr(pm, "TEST_MODE", False)
        monkeypatch.setattr(pm, "_bound_chain_addresses", _no_bindings)
        monkeypatch.setattr(pr.httpx, "AsyncClient", _no_network)
        order = create_multichain_order(
            "test-user-1",
            "premium_monthly",
            "ton_usdt",
            12.0,
            quoted_amount=12.003699,
            ttl_seconds=3600,
            extra={"memo": "cmabc", "micro": 12_003_699, "recv": "EQx"},
        )
        resp = self._client().post(
            "/api/premium/upgrade",
            json={
                "plan": "premium_monthly",
                "order_token": order["order_token"],
                "comment": "cmabc",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown order rail"
        assert not hasattr(pm, "RAIL_TON_USDT")
        assert not hasattr(pm, "verify_ton_usdt_payment")

    def test_payment_order_defaults_to_evm_usdc(self):
        """rail 未帶 → 預設 evm_usdc（env 未設收款地址 → 503 fail-closed）。"""
        import api.payment_rails as pr

        client = self._client()
        with (
            patch.object(pr, "EVM_USDC_RECEIVING_ADDRESS", ""),
            patch.object(pr, "EVM_RPC_URL", ""),
        ):
            resp = client.post(
                "/api/premium/payment-order",
                json={"plan": "premium_monthly"},
            )
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]

    def test_ton_order_endpoint_removed(self):
        """410 的 deprecation 窗口已過（2026-09-09 起），整支移除。"""
        import api.routers.premium as pm

        assert "/api/premium/ton-order" not in {r.path for r in pm.router.routes}
        assert not hasattr(pm, "TonOrderRequest")

    def test_pricing_lists_single_evm_rail_only(self):
        client = self._client()
        resp = client.get("/api/premium/pricing")
        assert resp.status_code == 200
        rails = resp.json()["rails"]
        assert [r["rail"] for r in rails] == ["evm_usdc"]
        # 論壇發文／打賞價在 forum 區塊（USD；2026-09-25 論壇改 USDC）
        assert "create_post" in resp.json()["forum"]["prices"]
