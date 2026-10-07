"""Settings 錢包卡不得只認 TON（2026-09-04）。

登入 2026-08-31 起統一 EVM，但兩個地方還只認 ton_wallet：

1. ``/api/user/me`` 只在 auth_method=="ton_wallet" 或 user_id 是 TON 前綴
   （UQ/EQ/0:/kQ）時回傳 wallet_address。EVM 使用者的 user_id 是
   ``evm_0x…``，對不上任何一條 → wallet_address 一律 None，也就是
   **每個登入的人**在 Settings 都看不到自己的地址。
2. 前端快取路徑的 has_wallet 也只認 ton_wallet，EVM 使用者會先閃一下
   「未綁定」才被後台 API 修正。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def me_source() -> str:
    src = (ROOT / "api/routers/user.py").read_text(encoding="utf-8")
    start = src.index('auth_method = current_user.get("auth_method")')
    return src[start : start + 1200]


class TestBackendResolvesEvmAddress:
    def test_evm_users_get_a_wallet_address(self, me_source):
        assert "evm_wallet" in me_source, "/api/user/me 沒有認 EVM 登入"
        assert 'startswith("evm_")' in me_source, "沒有從 evm_<addr> 還原地址"

    def test_ton_path_is_kept(self, me_source):
        """反向守衛：TON 使用者仍要拿得到地址，不可被一起改掉。"""
        assert 'auth_method == "ton_wallet"' in me_source
        # 2026-09-25：前綴清單（UQ/EQ/0:/kQ）漏了 0Q／-1:／Ef…，改成真的解碼判斷
        assert "is_ton_address(user_id)" in me_source


class TestFrontendCacheRecognisesBothChains:
    def test_has_wallet_accepts_evm(self):
        src = (ROOT / "web/js/auth.js").read_text(encoding="utf-8")
        block = src[src.index("async function loadSettingsWalletStatus"):][:1600]
        assert "evm_wallet" in block, "快取路徑只認 ton_wallet——EVM 使用者會閃一下未綁定"
        assert "ton_wallet" in block, "TON 也要留著"


class TestCardCopyIsChainNeutral:
    @pytest.mark.parametrize("loc", ("zh-TW", "zh-CN", "en", "ru"))
    def test_card_does_not_claim_a_single_chain(self, loc):
        """卡片顯示的是你登入用的那條鏈的地址，標題不能寫死某一條。"""
        import json

        w = json.loads((ROOT / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))
        card = w["settings"]["wallet"]
        for key in ("title", "description", "linkButton", "connected", "reloginHint"):
            text = card.get(key, "")
            assert not re.search(r"\bTON\b", text, re.I), (
                f"{loc}.settings.wallet.{key} 仍指名 TON，但這張卡顯示的是"
                f"帳號登入錢包（可能是 EVM）：{text!r}"
            )

    def test_html_fallback_matches(self):
        """i18n 掛掉時看到的內建 fallback 也不能寫 TON Wallet。"""
        src = (ROOT / "web/js/components/tab-settings.js").read_text(encoding="utf-8")
        block = src[src.index('data-i18n="settings.wallet.title"'):][:400]
        assert "TON" not in block
