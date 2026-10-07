"""WC 連線遙測端點 — 前端無聲卡死模式的可 grep 記錄（2026-09-05）。

背景：EVM WalletConnect 掃碼登入的三個無聲失敗（死 QR、連上被誤判取消、
簽名卡死）全部發生在前端（瀏覽器↔relay），後端 log 一片空白——事後想判讀
只能請使用者開 ?wc-debug=1 重現。這個端點讓前端把失敗模式回報成一行
[WcConnect]，與 FlagSnapshot／RunMetrics 同一套「讓看不見的失敗說話」。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _make_app():
    from fastapi import FastAPI

    from api.routers import user as user_module

    app = FastAPI()
    app.include_router(user_module.router)
    return app


class TestWcConnectEvent:
    def test_valid_event_is_logged(self, caplog):
        client = TestClient(_make_app())
        with caplog.at_level("INFO"):
            resp = client.post(
                "/api/user/wc-connect-event",
                json={
                    "mode": "pairing-dead",
                    "summary": "pairing=0(uri=match,wallet=offline)",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "[WcConnect]" in caplog.text
        assert "mode=pairing-dead" in caplog.text
        assert "wallet=offline" in caplog.text

    @pytest.mark.parametrize("mode", ["nonsense", "", "SIGN-TIMEOUT", "sql;--"])
    def test_unknown_mode_rejected(self, mode):
        """mode 是關閉式列舉——前端以外的东西不收，log 端才能信。"""
        client = TestClient(_make_app())
        resp = client.post("/api/user/wc-connect-event", json={"mode": mode})
        assert resp.status_code == 422

    def test_overlong_summary_rejected(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/user/wc-connect-event",
            json={"mode": "cancel-close", "summary": "x" * 301},
        )
        assert resp.status_code == 422

    def test_summary_optional(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/user/wc-connect-event", json={"mode": "sign-timeout"}
        )
        assert resp.status_code == 200

    def test_sign_resent_mode_accepted(self, caplog):
        """2026-09-05 前景重送機制的遙測：使用者切回頁面→立即重發。"""
        client = TestClient(_make_app())
        with caplog.at_level("INFO"):
            resp = client.post(
                "/api/user/wc-connect-event",
                json={"mode": "sign-resent", "summary": "visibilitychange resend #1"},
            )
        assert resp.status_code == 200
        assert "mode=sign-resent" in caplog.text


@pytest.mark.parametrize(
    "mode", ["sign-hex-fallback", "login-cancelled", "login-failed", "social-stalled"]
)
def test_login_outcome_modes_accepted(mode):
    """2026-09-24：Base App 登入失敗只在手機上 toast，後端查不到原因——登入收場要回報。"""
    client = TestClient(_make_app())
    resp = client.post(
        "/api/user/wc-connect-event",
        json={"mode": mode, "summary": "Invalid message code=-32603"},
    )
    assert resp.status_code == 200
