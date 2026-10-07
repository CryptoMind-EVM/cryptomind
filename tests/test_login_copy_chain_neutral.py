"""登入相關文案不得把某一條鏈講成唯一選項（2026-09-04）。

2026-08-31 起登入統一 EVM（index.html 的 DANNY 決策註解），但文案還停在
「連接 TON 錢包即可開始使用」「沒有 TON 錢包？下載 Tonkeeper」——後者更糟：
照著做也登不進去，因為 TON 登入入口已經移除。

順帶釘住死鍵：那兩條在全 repo 沒有任何渲染處，卻四個語系都有翻譯，等於
在維護沒人看得到的文案（而且會誤導下一個讀 i18n 的人以為那條路還在）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("zh-TW", "zh-CN", "en", "ru")
# 產品程式碼（排除 i18n 本身、備份與建置產物）
SOURCES = [ROOT / "web/index.html"] + [
    p for p in (ROOT / "web/js").rglob("*.js")
    if "i18n/" not in str(p) and "node_modules" not in str(p) and not p.name.endswith(".bak")
]


def _locale(name: str) -> dict:
    return json.loads((ROOT / f"web/js/i18n/{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source_blob() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in SOURCES)


class TestNoDeadLoginKeys:
    @pytest.mark.parametrize("loc", LOCALES)
    def test_every_login_key_is_rendered_somewhere(self, loc, source_blob):
        """login 命名空間很小且全是靜態引用——沒被引用就是死鍵。"""
        dead = [
            k for k in _locale(loc).get("login", {})
            if f"login.{k}" not in source_blob
        ]
        assert not dead, (
            f"{loc}.json 的 login.* 有死鍵（沒有任何 HTML/JS 渲染）：{dead}。"
            "刪掉它，不要翻譯沒人看得到的文案。"
        )


class TestLoginCopyDoesNotSingleOutOneChain:
    @pytest.mark.parametrize("loc", LOCALES)
    def test_entry_copy_is_chain_neutral(self, loc):
        """登入／訪客解鎖的入口文案不得指名單一鏈的錢包。

        「連接 EVM 錢包」這種**按鈕標籤**不在此限——那是在區分並列的兩個
        選項，不是把某條鏈講成唯一入口。
        """
        d = _locale(loc)
        entry = [
            d.get("login", {}).get("welcomeSubtitle", ""),
            d.get("chat", {}).get("guestLimitReached", ""),
            d.get("chat", {}).get("guestUnavailable", ""),
            d.get("chat", {}).get("guestConnect", ""),
        ]
        for text in entry:
            assert not re.search(r"\bTON\b", text, re.I), (
                f"{loc}：入口文案指名 TON——登入 2026-08-31 起統一 EVM，"
                f"照這句做會登不進去：{text!r}"
            )

    def test_removed_ton_login_button_stays_removed(self):
        """隱藏的死按鈕與其 click 委派不得復活（點不到卻還接著 handler）。"""
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        delegator = (ROOT / "web/js/click-delegator.js").read_text(encoding="utf-8")
        assert 'id="ton-login-btn"' not in html
        assert "safeTonLogin" not in delegator


class TestChainSpecificCopyIsKept:
    """反向守衛：真的在講某條鏈的文案不可以被一起掃掉。

    2026-09-09 訂閱統一後，付款幣別文案講的是 USDC on Base（stableEvmHint）；
    TON 專用工具與 ton_proof 驗證訊息——那些寫 TON 是正確的。
    """

    def test_payment_and_tool_copy_still_names_the_chain(self):
        d = _locale("zh-TW")
        # 付款幣別＋網路必須寫明（USDC on Base——訂閱唯一軌道）
        assert "USDC" in d["premium"]["stableEvmHint"], "付款幣別必須寫明"
        assert "Base" in d["premium"]["stableEvmHint"], "付款網路必須寫明"
        assert "TON" in d["tools"]["get_ton_balance"]["name"], "TON 專用工具必須寫明"
        # 並列按鈕標籤要保留鏈名——不然使用者分不出兩顆鈕的差別
        assert "EVM" in d["login"]["connectEvmWallet"]
