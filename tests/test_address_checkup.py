"""地址健診 v0 API 測試 — GET /api/scam-tracker/reports/check（design 2026-08-18）。

多源（社群舉報＋GoPlus＋TonAPI）合成判定；fail-soft：單源掛不掉整體，
且資訊不完整時判 caution 而非 no_red_flags（安全側傾斜）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.scam_tracker import router as scam_router

EVN_ADDR = "0x" + "12ab" * 10
TON_ADDR = "UQ" + "a" * 46


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(scam_router)
    return TestClient(app)


def _no_report():
    return {"success": True, "found": False}


class TestAddressCheckup:
    def test_evm_clean(self, client):
        """EVM 地址、無舉報、GoPlus 無旗標 → no_red_flags。"""
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.goplus.fetch_address_security_raw",
            return_value={"info": {}, "error": None},
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": EVN_ADDR})
        assert resp.status_code == 200
        data = resp.json()
        assert data["family"] == "evm"
        assert data["verdict"] == "no_red_flags"

    def test_evm_goplus_malicious_flag_high_risk(self, client):
        info = {"phishing_activities": "1", "sanctioned": "0"}
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.goplus.fetch_address_security_raw",
            return_value={"info": info, "error": None},
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": EVN_ADDR})
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "high_risk"
        assert any("phishing" in r for r in data["reasons"])

    def test_community_report_high_risk(self, client):
        report = {
            "id": 1,
            "scam_wallet_address": TON_ADDR,
            "verification_status": "verified",
        }
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[report]),
        ), patch(
            "core.tools.crypto_modules.ton_safety.assess_jetton_safety",
            return_value=SimpleNamespace(
                address=TON_ADDR, verification="none", symbol=None, name=None,
                holders_count=0, has_admin=False, exists=False, signals={}, source="tonapi",
            ),
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": TON_ADDR})
        assert resp.status_code == 200
        data = resp.json()
        assert data["family"] == "ton"
        assert data["verdict"] == "high_risk"
        assert data["sources"]["community"]["found"] is True

    def test_ton_blacklist_high_risk(self, client):
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.ton_safety.assess_jetton_safety",
            return_value=SimpleNamespace(
                address=TON_ADDR, verification="blacklist", symbol="SCAM",
                name="Scam Token", holders_count=42, has_admin=True, exists=True,
                signals={"admin_can_mint": True}, source="tonapi",
            ),
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": TON_ADDR})
        assert resp.json()["verdict"] == "high_risk"

    def test_source_down_returns_caution_not_clean(self, client):
        """源掛掉 ≠ 安全：必須 caution（安全側傾斜）。"""
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.goplus.fetch_address_security_raw",
            return_value={"info": None, "error": "GoPlus unavailable"},
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": EVN_ADDR})
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "caution"
        assert data["sources"]["goplus"]["status"] == "error"

    def test_source_error_does_not_leak_internals(self, client):
        """公開端點：源的原始錯誤（環境變數名、例外字串）只進 log，不回給前端。"""
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.goplus.fetch_address_security_raw",
            return_value={
                "info": None,
                "error": "GoPlus key not configured (GOPLUS_APP_KEY / GOPLUS_APP_SECRET)",
            },
        ):
            body = client.get(
                "/api/scam-tracker/reports/check", params={"address": EVN_ADDR}
            ).text
        assert "GOPLUS_APP" not in body
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.ton_safety.assess_jetton_safety",
            side_effect=RuntimeError("https://tonapi.io/v2/jettons?token=secret-ish 500"),
        ):
            body = client.get(
                "/api/scam-tracker/reports/check", params={"address": TON_ADDR}
            ).text
        assert "tonapi.io" not in body and "secret-ish" not in body

    def test_ton_admin_flag_caution(self, client):
        """TON jetton 有 admin 權限（黃旗）→ caution。"""
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.ton_safety.assess_jetton_safety",
            return_value=SimpleNamespace(
                address=TON_ADDR, verification="none", symbol="T", name="Token",
                holders_count=100, has_admin=True, exists=True, signals={}, source="tonapi",
            ),
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": TON_ADDR})
        assert resp.json()["verdict"] == "caution"

    def test_invalid_address_422(self, client):
        for bad in ("not-an-address", "0x123", "EQshort", " "):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": bad})
            assert resp.status_code == 422, bad

    def test_raw_address_0x_colon_accepted(self, client):
        """TON raw 格式 0:<64hex> 也接受（friendly 轉換前的常見貼上格式）。"""
        raw = "0:" + "b" * 64
        with patch(
            "api.routers.scam_tracker.reports.scam_tracker_repo.find_reports_by_address",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.tools.crypto_modules.ton_safety.assess_jetton_safety",
            return_value=SimpleNamespace(
                address=raw, verification="none", symbol=None, name=None,
                holders_count=0, has_admin=False, exists=False, signals={}, source="tonapi",
            ),
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": raw})
        assert resp.status_code == 200
        assert resp.json()["family"] == "ton"


class TestRouterRegistration:
    def test_scam_tracker_router_registered_by_default(self):
        """健診 v0 依賴 scam-tracker API——預設必須註冊（2026-08-13 曾整叢集關閉）。

        env SCAM_TRACKER_API_ENABLED=0 可回滾。以請求驗證（此 FastAPI 版本
        include_router 產生 _IncludedRouter，app.routes 不直接攤平 path）。
        """
        from fastapi.testclient import TestClient

        import api_server

        # 不用 with（跳過 lifespan——本機無測試 DB）；路由分發不需要 startup
        tc = TestClient(api_server.app)
        # 路由存在但地址無效 → 422（不存在會是 404）
        resp = tc.get("/api/scam-tracker/reports/check", params={"address": "bad"})
        assert resp.status_code == 422
