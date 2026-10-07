"""過時文案守衛（2026-09-24）：使用者看得到、但承諾了已不存在功能的字串。

- TON 錢包登入 2026-09-08 移除；登入 = EVM 錢包（SIWE）或 Telegram Mini App。
  訪客 prompt 還叫人「連 TON 錢包」、還提「risk-gated swaps」。
- 換幣執行（Omniston）已移除；平台是資料與分析工具，不代管資金、不代為交易。
- Premium 比較表的「每週安全摘要」從未實作：HTML 列 265cb63 已拿掉，
  i18n key 殘留四語。
- 論壇儀表板錢包卡標題寫「TON 錢包」，但按鈕是 EVM 登入；Telegram 登入
  失敗的 toast 寫「TON 登入失敗」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from api.routers import guest as guest_router

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("en", "zh-TW", "zh-CN", "ru")
TON = re.compile(r"\bTON\b")


def _locale(loc: str) -> dict:
    return json.loads((ROOT / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))


class TestGuestPrompt:
    PROMPT = guest_router._GUEST_SYSTEM_PROMPT

    def test_no_ton_wallet_or_swap(self):
        assert not re.search(r"TON\s+wallet", self.PROMPT, re.I)
        assert "swap" not in self.PROMPT.lower()

    def test_does_not_push_wallet_login(self):
        """2026-09-27（上市準備 PR-4）：回答不再叫人連錢包；只有問到自己的持倉才提登入。"""
        assert "connect" not in self.PROMPT.lower()
        assert "EVM wallet" not in self.PROMPT
        assert "signing in lets CryptoMind remember their holdings" in self.PROMPT

    def test_keeps_compliance_stance(self):
        assert "never holds user funds" in self.PROMPT
        assert "never executes" in self.PROMPT
        assert "Not financial advice" in self.PROMPT

    def test_error_details_do_not_say_ton_wallet(self):
        """429／503 的 detail 是 API 回給訪客的字串。"""
        src = (ROOT / "api/routers/guest.py").read_text(encoding="utf-8")
        assert not re.search(r"TON\s+wallet", src, re.I)


class TestPremiumCompareTable:
    @pytest.mark.parametrize("loc", LOCALES)
    def test_every_compare_key_is_a_rendered_row(self, loc):
        """比較表的 key 只能對應實際渲染的列，殘留 key 就是沒兌現的承諾。"""
        html = (ROOT / "web/forum/premium.html").read_text(encoding="utf-8")
        keys = [k for k in _locale(loc)["premium"] if k.startswith("compare")]
        orphans = [k for k in keys if f'data-i18n="premium.{k}"' not in html]
        assert not orphans, f"{loc}: premium.html 沒渲染 {orphans}"


class TestNoTonLoginLabels:
    @pytest.mark.parametrize("loc", LOCALES)
    def test_labels_are_chain_neutral(self, loc):
        d = _locale(loc)
        for path, text in (
            ("forum.dash.tonWallet", d["forum"]["dash"]["tonWallet"]),
            ("tonAuth.loginFailedShort", d["tonAuth"]["loginFailedShort"]),
        ):
            assert not TON.search(text), f"{loc}.{path} 仍指名 TON：{text!r}"

    def test_dashboard_fallback(self):
        html = (ROOT / "web/forum/dashboard.html").read_text(encoding="utf-8")
        assert 'data-i18n="forum.dash.tonWallet">Wallet</span>' in html

    def test_guest_flow_fallbacks(self):
        src = (ROOT / "web/js/chat-analysis.js").read_text(encoding="utf-8")
        block = src[
            src.index("async function sendGuestMessage") : src.index(
                "window._appendConnectCta"
            )
        ]
        assert not TON.search(block)

    def test_settings_wallet_connected_fallback(self):
        src = (ROOT / "web/js/components/tab-settings.js").read_text(encoding="utf-8")
        assert 'data-i18n="settings.wallet.connected">Wallet connected<' in src


class TestHomepageMeta:
    """首頁 description／og／twitter 會被連結預覽與 AI 目錄抓去當介紹（2026-09-24）。

    「intelligent trading recommendations」違反非投顧定位（[[compliance-positioning]]），
    「connect a TON or EVM wallet」是已移除的 TON 登入。
    """

    META = re.compile(
        r'<meta\s+(?:name|property)="(description|keywords|og:description|'
        r'twitter:description)"\s+content="([^"]*)"'
    )
    BANNED = re.compile(
        r"trading recommendation|trading advice|signals?\b|\bTON\b", re.IGNORECASE
    )

    def _metas(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        return dict(self.META.findall(html))

    def test_all_four_present(self):
        assert set(self._metas()) == {
            "description",
            "keywords",
            "og:description",
            "twitter:description",
        }

    @pytest.mark.parametrize(
        "name", ["description", "keywords", "og:description", "twitter:description"]
    )
    def test_no_advice_or_ton_login_claims(self, name):
        content = self._metas()[name]
        assert not self.BANNED.search(content), f"{name}: {content!r}"

    def test_description_carries_disclaimer(self):
        assert "not financial advice" in self._metas()["description"]
