"""EVM／站台設定跟 TON 名稱脫鉤＋Premium 的 TON 殘骸清除（TON 盤查 A3/A4/C2/C3/C5）。

- A4：NETWORK／SITE_URL／PREMIUM_USD_PRICES 是新名字，沒設就沿用舊的 TON_* env
  ——正式站現在只設 TON_NETWORK=mainnet，解析結果必須跟以前一模一樣
  （SIWE chain id 8453、Base mainnet USDC 合約）。
- A3：USDC 訂閱寫進 membership_payments.amount 的是實收美元（訂單 micro），
  不是 system_config 那個 TON 定價 × 月數。
- C2：premium 的 ton_native claim 分支、rail 常數、平台能力鍵都拿掉；舊 token／
  舊請求一律乾淨的 4xx。
- C3：/pricing 不再算 premium 的 TON 金額（也不再每次打 TON/USD 報價）；
  論壇價格 2026-09-25 改成 USD 的 forum.prices（ton 區塊移除）。
- C5：沒人用的 TON_PREMIUM_TON_FLOOR 移除。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]

BASE_MAINNET_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


# --------------------------------------------------------------------------- #
# A4：設定解析（子行程，env 完全由這裡決定，不吃本機 .env）                      #
# --------------------------------------------------------------------------- #

_PROBE = r"""
import json
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
import core.config as c
from api import evm_verification as ev
print(json.dumps({
    "network": c.NETWORK,
    "is_testnet": c.IS_TESTNET,
    "ton_network": c.TON_NETWORK,
    "ton_is_testnet": c.TON_IS_TESTNET,
    "siwe_chain_id": ev.SIWE_CHAIN_ID,
    "usdc": c.EVM_USDC_CONTRACT,
    "rpc": c.EVM_RPC_URL,
    "site_url": c.SITE_URL,
    "prices": c.PREMIUM_USD_PRICES,
    "legacy_prices_same_object": c.TON_PREMIUM_USD_ANCHOR is c.PREMIUM_USD_PRICES,
    "has_ton_floor": hasattr(c, "TON_PREMIUM_TON_FLOOR"),
}))
"""

_CONTROLLED = (
    "NETWORK",
    "TON_NETWORK",
    "EVM_RPC_URL",
    "EVM_USDC_CONTRACT",
    "SITE_URL",
    "TON_MANIFEST_URL",
    "PREMIUM_MONTHLY_USD",
    "PREMIUM_YEARLY_USD",
    "TON_PREMIUM_MONTHLY_USD",
    "TON_PREMIUM_YEARLY_USD",
)


def _resolve(**env: str) -> dict:
    full = {k: v for k, v in os.environ.items() if k not in _CONTROLLED}
    full.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestConfigResolution:
    def test_current_prod_env_still_resolves_to_base_mainnet(self):
        """正式站今天只設 TON_NETWORK=mainnet（其餘新變數都沒設）。"""
        got = _resolve(TON_NETWORK="mainnet")
        assert got["network"] == "mainnet" and got["is_testnet"] is False
        assert got["siwe_chain_id"] == 8453
        assert got["usdc"] == BASE_MAINNET_USDC
        assert got["rpc"] == "https://mainnet.base.org"
        # TON 專用工具沿用的別名跟著同一個來源
        assert got["ton_network"] == "mainnet" and got["ton_is_testnet"] is False
        # 其餘新設定也回到舊預設
        assert got["site_url"] == "https://getcryptomind.com"
        assert got["prices"] == {"premium_monthly": 12.0, "premium_yearly": 108.0}
        assert got["legacy_prices_same_object"] is True
        # C5
        assert got["has_ton_floor"] is False

    def test_nothing_set_is_testnet(self):
        got = _resolve()
        assert got["network"] == "testnet" and got["is_testnet"] is True
        assert got["siwe_chain_id"] == 84532
        assert got["usdc"] == BASE_SEPOLIA_USDC
        assert got["rpc"] == "https://sepolia.base.org"

    def test_network_wins_over_ton_network(self):
        got = _resolve(NETWORK="mainnet", TON_NETWORK="testnet")
        assert got["siwe_chain_id"] == 8453 and got["usdc"] == BASE_MAINNET_USDC
        assert got["ton_network"] == "mainnet", "TON 別名跟 NETWORK 同源"

        got = _resolve(NETWORK="testnet", TON_NETWORK="mainnet")
        assert got["siwe_chain_id"] == 84532 and got["usdc"] == BASE_SEPOLIA_USDC

    def test_site_url_and_prices_fall_back_to_legacy_env(self):
        got = _resolve(
            TON_MANIFEST_URL="https://legacy.example",
            TON_PREMIUM_MONTHLY_USD="15",
            TON_PREMIUM_YEARLY_USD="150",
        )
        assert got["site_url"] == "https://legacy.example"
        assert got["prices"] == {"premium_monthly": 15.0, "premium_yearly": 150.0}

    @pytest.mark.parametrize(
        "env",
        [
            {"NETWORK": " mainnet "},
            {"NETWORK": "mainnet\n"},
            {"TON_NETWORK": " mainnet"},
        ],
    )
    def test_whitespace_in_network_does_not_flip_to_testnet(self, env):
        """env 面板多一個空白就整個平台跑到 testnet（錯的 RPC／USDC 合約／chain id）。
        這專案出過兩次 env 黏行事故，網路模式要容錯。"""
        got = _resolve(**env)
        assert got["network"] == "mainnet" and got["is_testnet"] is False
        assert got["siwe_chain_id"] == 8453

    def test_new_names_win(self):
        got = _resolve(
            SITE_URL="https://new.example",
            TON_MANIFEST_URL="https://legacy.example",
            PREMIUM_MONTHLY_USD="20",
            TON_PREMIUM_MONTHLY_USD="15",
        )
        assert got["site_url"] == "https://new.example"
        assert got["prices"]["premium_monthly"] == 20.0
        assert got["prices"]["premium_yearly"] == 108.0


# --------------------------------------------------------------------------- #
# A4：站台網域 helper 搬到 public_base（中性名稱），舊名保留別名                 #
# --------------------------------------------------------------------------- #


class TestSiteDomain:
    def test_domain_comes_from_site_url_with_port(self, monkeypatch):
        import api.public_base as pb

        monkeypatch.setattr(pb, "SITE_URL", "http://localhost:8080")
        assert pb.expected_site_domain() == "localhost:8080"
        monkeypatch.setattr(pb, "SITE_URL", "https://getcryptomind.com")
        assert pb.expected_site_domain() == "getcryptomind.com"

    def test_production_rejects_non_https_site_url(self, monkeypatch):
        import api.public_base as pb

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setattr(pb, "SITE_URL", "http://getcryptomind.com")
        with pytest.raises(HTTPException) as exc:
            pb.expected_site_domain()
        assert exc.value.status_code == 500
        assert "TON" not in exc.value.detail

    def test_ton_verification_module_removed(self):
        # 2026-09-26：TON 登入、付款都沒了，api/ton_verification.py 已無呼叫端而移除
        import importlib.util

        assert importlib.util.find_spec("api.ton_verification") is None

    def test_evm_login_fallback_domain_uses_site_url(self, monkeypatch):
        import api.evm_verification as ev
        import api.public_base as pb

        monkeypatch.setattr(pb, "SITE_URL", "https://site.example")
        assert ev._expected_domain() == "site.example"

    def test_public_base_url_falls_back_to_site_url(self, monkeypatch):
        import api.public_base as pb

        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        monkeypatch.setattr(pb, "SITE_URL", "https://site.example/")
        assert pb.public_base_url() == "https://site.example"
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://cron.example")
        assert pb.public_base_url() == "https://cron.example"

    def test_robots_and_sitemap_use_site_url(self, monkeypatch):
        from fastapi.testclient import TestClient

        import api_server

        monkeypatch.setattr(api_server, "SITE_URL", "https://site.example")
        client = TestClient(api_server.app)
        robots = client.get("/robots.txt").text
        assert "Sitemap: https://site.example/sitemap.xml" in robots
        sitemap = client.get("/sitemap.xml").text
        assert "<loc>https://site.example/</loc>" in sitemap


# --------------------------------------------------------------------------- #
# premium router 共用                                                          #
# --------------------------------------------------------------------------- #


def _premium_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.routers.premium as pm
    from api.deps import get_current_user

    app = FastAPI()
    app.include_router(pm.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user-1"}
    return TestClient(app)


def _sign_raw_order(payload: dict) -> str:
    """造一張沒有 rail 欄位的舊式 token（create_multichain_order 一定會帶 rail）。"""
    import api.evm_verification as ev

    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode() + "." + ev._sign(raw)


class TestPremiumNetworkLabels:
    def test_pricing_rail_network_follows_is_testnet(self, monkeypatch):
        import api.routers.premium as pm

        client = _premium_client()
        monkeypatch.setattr(pm, "IS_TESTNET", False)
        assert (
            client.get("/api/premium/pricing").json()["rails"][0]["network"] == "base"
        )
        monkeypatch.setattr(pm, "IS_TESTNET", True)
        assert (
            client.get("/api/premium/pricing").json()["rails"][0]["network"]
            == "base_sepolia"
        )

    def test_payment_order_network_follows_is_testnet(self, monkeypatch):
        import api.payment_rails as pr
        import api.routers.premium as pm

        monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", "0x" + "1" * 40)
        monkeypatch.setattr(pr, "EVM_RPC_URL", "https://rpc.example")
        monkeypatch.setattr(pm.payment_order_repo, "create", AsyncMock(return_value={}))
        monkeypatch.setattr(pm, "IS_TESTNET", False)
        resp = _premium_client().post(
            "/api/premium/payment-order", json={"plan": "premium_monthly"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["network"] == "base"


# --------------------------------------------------------------------------- #
# C2：premium 的 ton_native claim 分支移除                                      #
# --------------------------------------------------------------------------- #


class TestTonNativeRailRemoved:
    def test_payment_order_ton_native_is_validation_error(self):
        resp = _premium_client().post(
            "/api/premium/payment-order",
            json={"plan": "premium_monthly", "rail": "ton_native"},
        )
        assert resp.status_code == 422

    def _no_ton_verification(self, monkeypatch):
        # TON 驗證模組已移除（見 test_ton_verification_module_removed）；關掉 TEST_MODE
        # 讓舊 TON 訂單真的走一次 rail 分派
        import api.routers.premium as pm

        monkeypatch.setattr(pm, "TEST_MODE", False)

    def test_claim_with_ton_native_token_is_unknown_rail(self, monkeypatch):
        from api.evm_verification import create_multichain_order

        self._no_ton_verification(monkeypatch)
        order = create_multichain_order(
            "test-user-1",
            "premium_monthly",
            "ton_native",
            12.0,
            quoted_amount=6.0,
            ttl_seconds=3600,
            extra={"c": "cmabc"},
        )
        resp = _premium_client().post(
            "/api/premium/upgrade",
            json={
                "plan": "premium_monthly",
                "order_token": order["order_token"],
                "comment": "cmabc",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown order rail"

    def test_claim_with_railless_legacy_token_is_unknown_rail(self, monkeypatch):
        self._no_ton_verification(monkeypatch)
        token = _sign_raw_order(
            {
                "u": "test-user-1",
                "p": "premium_monthly",
                "a": 6.0,
                "c": "cmabc",
                "e": int(time.time()) + 3600,
            }
        )
        resp = _premium_client().post(
            "/api/premium/upgrade",
            json={"plan": "premium_monthly", "order_token": token, "comment": "cmabc"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown order rail"

    def test_ton_native_symbols_gone(self):
        import api.payment_rails as pr
        import api.routers.premium as pm
        from core import platform

        assert not hasattr(pr, "RAIL_TON_NATIVE")
        assert not hasattr(pm, "RAIL_TON_NATIVE")
        for caps in platform.CAPABILITIES.values():
            assert "rail_ton_native" not in caps
        for plat in platform.PLATFORMS:
            assert platform.rail_allowed(plat, "ton_native") is False
        assert pr.rail_available("ton_native") is False



# --------------------------------------------------------------------------- #
# C3：/pricing 不再算 premium 的 TON 金額                                        #
# --------------------------------------------------------------------------- #


class TestPricingWithoutPremiumTon:
    def test_no_ton_usd_lookup_and_only_forum_prices(self, monkeypatch):
        import api.routers.premium as pm
        import core.tools.crypto_modules.ton_price as tp

        def _boom():
            raise AssertionError("/pricing 不該再查 TON/USD")

        monkeypatch.setattr(tp, "get_ton_usd_price", _boom)
        resp = _premium_client().get("/api/premium/pricing")
        assert resp.status_code == 200
        data = resp.json()
        # 2026-09-25 論壇改 USDC：ton 區塊整個拿掉，換成中性的 forum（USD）
        assert "ton" not in data
        forum = data["forum"]
        assert forum["asset"] == "USDC"
        assert set(forum["prices"]) == {"create_post", "tip", "tip_min", "tip_max"}
        assert set(data["pricing"]["premium"]) == {"monthly", "yearly"}
        assert not hasattr(pm, "_resolve_premium_ton_amount")
        assert not hasattr(pm, "TON_QUOTE_EXPIRY_MINUTES")

    def test_forum_prices_are_usd_config(self, monkeypatch):
        import api.routers.premium as pm

        monkeypatch.setattr(pm, "FORUM_POST_FEE_USD", 0.75)
        prices = _premium_client().get("/api/premium/pricing").json()["forum"]["prices"]
        assert prices["create_post"] == 0.75
        assert prices["tip"] == 1.0
        assert prices["tip_min"] == 0.1 and prices["tip_max"] == 100.0

    def test_forum_config_reads_forum_prices_from_pricing(self):
        js = (REPO / "web/js/forum-config.js").read_text(encoding="utf-8")
        assert "AppAPI.get('/api/premium/pricing')" in js
        assert "data.forum && data.forum.prices" in js
        assert "data.ton" not in js
        assert "TON" not in js, "論壇價格不再以 TON 顯示"


# --------------------------------------------------------------------------- #
# A3：USDC 訂閱記實收美元                                                       #
# --------------------------------------------------------------------------- #


class _FakeConn:
    """upgrade_to_pro 用的最小 DB 替身：記下 INSERT membership_payments 的參數。"""

    def __init__(self):
        self.inserts = []
        conn = self

        class _Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                self._row = None
                if "SELECT user_id FROM membership_payments" in sql:
                    self._row = None
                elif "FROM users WHERE user_id" in sql:
                    self._row = ("free", None, False)
                elif "INSERT INTO membership_payments" in sql:
                    conn.inserts.append((sql, params))

            def fetchone(self):
                return self._row

        self._cur = _Cur()

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestPaymentAmountRecording:
    def test_given_amount_is_recorded_without_ton_price_lookup(self, monkeypatch):
        import core.database.system_config as sc
        import core.database.user as user_db

        conn = _FakeConn()
        monkeypatch.setattr(user_db, "get_connection", lambda: conn)

        def _no_prices():
            raise AssertionError("給了實收金額就不該再查 TON 定價")

        monkeypatch.setattr(sc, "get_prices", _no_prices)
        tx = "0x" + "a" * 64
        assert user_db.upgrade_to_pro("u1", 12, tx, amount=108.004321) is True
        (sql, params) = conn.inserts[0]
        assert params == ("u1", 108.004321, 12, tx)

    def test_default_amount_unchanged_for_other_callers(self, monkeypatch):
        """admin 授予／TEST_MODE 等沒帶 amount 的呼叫：沿用舊算法、同一句 SQL。"""
        import core.database.system_config as sc
        import core.database.user as user_db

        conn = _FakeConn()
        monkeypatch.setattr(user_db, "get_connection", lambda: conn)
        monkeypatch.setattr(sc, "get_prices", lambda: {"premium": 6.0})
        user_db.upgrade_to_pro("u1", 12, "admin_grant_a_u1_1")
        (sql, params) = conn.inserts[0]
        assert params == ("u1", 72.0, 12, "admin_grant_a_u1_1")
        assert "(user_id, amount, months, tx_hash, created_at)" in sql

    def test_usdc_claim_passes_amount_charged(self, monkeypatch):
        import api.routers.premium as pm
        from api.evm_verification import create_multichain_order

        payer = "0x" + "2" * 40
        tx = "0x" + "b" * 64
        captured = {}

        async def _bound(user_id, chain):
            return [payer]

        def _fake_upgrade(**kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr(pm, "TEST_MODE", False)
        monkeypatch.setattr(pm, "_bound_chain_addresses", _bound)
        monkeypatch.setattr(
            pm,
            "verify_evm_usdc_payment",
            AsyncMock(return_value={"tx_hash": tx, "amount_micro": 12_003_699}),
        )
        monkeypatch.setattr(pm, "upgrade_to_pro", _fake_upgrade)
        monkeypatch.setattr(
            pm.user_repo,
            "get_membership",
            AsyncMock(return_value={"membership_tier": "free"}),
        )
        order = create_multichain_order(
            "test-user-1",
            "premium_monthly",
            "evm_usdc",
            12.0,
            quoted_amount=12.003699,
            ttl_seconds=3600,
            extra={"memo": "cmabc", "micro": 12_003_699, "recv": "0x" + "1" * 40},
        )
        resp = _premium_client().post(
            "/api/premium/upgrade",
            json={"plan": "premium_monthly", "order_token": order["order_token"]},
        )
        assert resp.status_code == 200, resp.text
        assert captured["tx_hash"] == tx
        assert captured["amount"] == 12.003699, (
            "記實收的 USDC（含唯一尾數），不是 TON 價"
        )

    def test_historical_rows_still_display(self, monkeypatch):
        from core.database.forum import annotate_payment_row

        monkeypatch.setattr(
            "core.config.PREMIUM_USD_PRICES",
            {"premium_monthly": 12.0, "premium_yearly": 108.0},
        )
        ton = annotate_payment_row(
            {"type": "membership", "amount": 6.0, "months": 1, "tx_hash": "te6cck"}
        )
        assert ton["asset"] == "TON" and ton["display_amount"] == 6.0
        usdc = annotate_payment_row(
            {
                "type": "membership",
                "amount": 0.3,
                "months": 1,
                "tx_hash": "0x" + "d" * 64,
            }
        )
        assert usdc["asset"] == "USDC"
        assert usdc["display_amount"] == 12.0, (
            "舊 USDC 列存的是 TON 價，顯示照舊走錨定價"
        )
