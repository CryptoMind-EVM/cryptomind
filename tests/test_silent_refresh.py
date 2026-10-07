"""Silent refresh 回歸測試（2026-08-22，DANNY 核准）。

問題：token 過期後使用者被自動登出——真兇是 notification-service.js
的三處激進行為（本地過期即 clearExpiredToken／401 即 clear／
!accessTokenExpiry 判過期），它們繞過 AppAPI 權威的
401→refresh→retry 鏈，在通知輪詢裡就把全域 session 清掉。

修復後的不變量（本測試把關）：
1. notification-service.js 不得呼叫 clearExpiredToken（全域登出決策
   只能留給 auth.js 本體與 api-client 的權威鏈）
2. 過期路徑應觸發 backendTokenRefresh（silent refresh）
3. api-client.js 的 401→refresh→retry 鏈必須存在（防退化）
4. 後端 /api/user/refresh 必須支援 HttpOnly cookie fallback
   （前端 body=null 的刷新呼叫靠它）
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _read(p: str) -> str:
    return (ROOT / p).read_text(encoding="utf-8")


class TestNotificationServiceNoForceLogout:
    SRC = "web/js/notification-service.js"

    def test_no_clear_expired_token(self):
        import re

        src = _read(self.SRC)
        calls = re.findall(r"clearExpiredToken\s*\(\s*\)", src)
        assert not calls, (
            "notification-service 不得呼叫 clearExpiredToken()——"
            "通知輪詢沒有全域登出權（silent refresh 2026-08-22 修復回歸）。"
            f"發現 {len(calls)} 處呼叫"
        )

    def test_expired_path_triggers_silent_refresh(self):
        src = _read(self.SRC)
        assert "backendTokenRefresh" in src, (
            "過期路徑應背景觸發單飛 refresh，而不是登出"
        )

    def test_missing_expiry_not_treated_as_expired(self):
        src = _read(self.SRC)
        # 舊版寫法：!expiry → return true（舊格式 session 直接判過期→登出）
        assert "return true; // 沒有過期時間" not in src


class TestApiClientRefreshChain:
    SRC = "web/js/api-client.js"

    def test_401_refresh_retry_chain_exists(self):
        src = _read(self.SRC)
        assert "backendTokenRefresh" in src, "401→refresh 鏈必須存在"
        assert "_authRetried" in src, "refresh 後 retry 必須防迴圈"

    def test_refresh_endpoint_skip_in_401_handler(self):
        src = _read(self.SRC)
        assert "/api/user/refresh" in src, "refresh 端點本身要 skip（防死鎖）"


class TestBackendRefreshCookieFallback:
    SRC = "api/routers/user.py"

    def test_refresh_reads_httponly_cookie_fallback(self):
        src = _read(self.SRC)
        assert "request.cookies.get(REFRESH_TOKEN_COOKIE)" in src, (
            "/api/user/refresh 必須支援 HttpOnly cookie fallback——"
            "前端刷新（body=null）依賴它"
        )
