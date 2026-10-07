"""ton-auth.js 的職責邊界（2026-09-08 重寫）。

這支從前是「TON Connect 登入」模組。2026-08-31 登入統一走 EVM 之後，
index.html 的登入 modal 就沒有 TON 錢包按鈕了，整條 TON 錢包登入鏈變成
沒有任何呼叫端的死碼（只剩兩則過期註解提到它）。2026-09-08 刪除。

本檔原本的兩條測試守的正是被刪掉的那些函式（``_completeLoginFromWallet``、
``_applyTonSession``），跟著一起換掉——留著只會擋住清理，而且會給人
「TON 登入還活著」的錯覺。

2026-09-26：付款全部改成 Base USDC，TON Connect 單例也沒有呼叫端了，連同
index.html 的 TON Connect SDK／tonweb 一起移除。這支只剩 Telegram 登入。

現在守的是：
1. TON Connect 不會悄悄長回來（這支和 index.html 都不再載入／建立它）。
2. TON 登入鏈不會回來。
3. Telegram 登入還在。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TON_AUTH = (ROOT / "web/js/ton-auth.js").read_text(encoding="utf-8")


class TestTonConnectRemoved:
    """TON Connect 已無呼叫端（2026-09-26）——不能再建單例或載 SDK。"""

    @pytest.mark.parametrize(
        "symbol",
        ["TON_CONNECT_UI", "TonConnectUI", "window.tonConnectUI", "getTonConnectUI"],
    )
    def test_no_ton_connect_in_ton_auth(self, symbol):
        # 檔頭說明文字會提到 window.tonConnectUI，只檢查程式碼行
        code = "\n".join(
            line for line in TON_AUTH.splitlines() if not line.lstrip().startswith("//")
        )
        assert symbol not in code

    def test_index_html_does_not_load_ton_sdks(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        assert "tonconnect-ui" not in html
        assert "tonweb" not in html

    def test_manifest_route_removed(self):
        src = (ROOT / "api_server.py").read_text(encoding="utf-8")
        assert "/tonconnect-manifest.json" not in src


class TestLoginChainStaysDeleted:
    """登入走 EVM。TON 登入鏈不准回來。"""

    @pytest.mark.parametrize(
        "symbol",
        [
            "function handleTonLogin",
            "window.safeTonLogin",
            "window.safePiLogin",
            "function _applyTonSession",
            "function _completeLoginFromWallet",
            "function _attachResumeLoginListener",
            "function rawToFriendly",
        ],
    )
    def test_removed_login_symbol_is_not_back(self, symbol):
        assert symbol not in TON_AUTH, (
            f"{symbol} 回來了——登入請走 evm-auth.js；"
            f"TON Connect 在這支只服務付款"
        )

    def test_frontend_never_calls_the_ton_login_endpoint(self):
        # 後端 /api/user/ton-login 還在（見下方 docstring），但前端不該再打它
        assert "/api/user/ton-login" not in TON_AUTH
        assert "ton-proof-payload" not in TON_AUTH


class TestTelegramLoginIntact:
    """Mini App 的 Telegram 原生登入是這支唯一還活著的登入路徑。"""

    def test_telegram_entry_points_present(self):
        for symbol in (
            "function _applyTelegramSession",
            "async function handleTelegramLogin",
            "window.handleTelegramLogin",
            "window.safeTelegramLogin",
        ):
            assert symbol in TON_AUTH, symbol

    def test_success_ui_is_no_longer_ton_specific(self):
        # 共用的成功 UI 現在只剩 Telegram 用——名字不該再叫 TON
        assert "_onLoginSuccessUI" in TON_AUTH
        assert "_onTonLoginSuccessUI" not in TON_AUTH
