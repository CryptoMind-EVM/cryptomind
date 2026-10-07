"""訂閱價顯示單一擁有者：PremiumManager（USD 錨定，USDC on Base）。

2026-09-09 訂閱統一 USDC on Base（PR #719）後的兩個顯示一致性問題：

1. premium.js 在 SPA 是 settings 分頁的 lazy 模組（spa.js _TAB_MODULES），
   載入時 DOMContentLoaded 早已觸發——init 的監聽器永遠不執行，
   _ensurePricing 不跑 → Settings CTA 的 data-price="premium" 永遠停在
   Loading（DANNY 回報的卡 loading 真根因）。需要 readyState 判斷。
2. forum-config.js 的 updatePriceDisplays() 會用 TON 動態價（8.7591 TON）
   覆寫同一個 [data-price="premium"]——統一 USDC 後 TON 已不能付訂閱，
   顯示它就是誤導（premium.html 方案卡上兩個寫入者還會互相賽跑）。
   訂閱鍵（premium/premium_yearly）必須跳過，留給 PremiumManager。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestPremiumManagerLazyInit:
    def test_ready_state_guard_for_lazy_module(self):
        """lazy 模組載入時 DOMContentLoaded 已過——要 fallback 立即 init。"""
        src = _read("web/js/premium.js")
        block = re.search(r"initEventListeners\(\) \{.*?\n    \}", src, re.S)
        assert block, "找不到 initEventListeners"
        body = block.group(0)
        assert "document.readyState === 'loading'" in body
        assert "addEventListener('DOMContentLoaded'" in body, (
            "首屏載入路徑（readyState=loading）仍要走事件監聽"
        )
        # else 分支要直接執行 init（不能只註冊監聽器）
        assert re.search(r"\} else \{\s*\n\s*onReady\(\);", body), (
            "readyState 已完成時必須立即執行 init，否則 lazy 模組永不初始化"
        )

    def test_lazy_init_fetches_pricing(self):
        src = _read("web/js/premium.js")
        assert re.search(r"onReady.*_ensurePricing", src, re.S), (
            "init 路徑要觸發 _ensurePricing（否則 CTA 永遠 Loading）"
        )


class TestSubscriptionPriceOwnership:
    def test_forum_config_skips_subscription_keys(self):
        """forum-config 的 TON 價格不得覆寫訂閱價顯示。"""
        src = _read("web/js/forum-config.js")
        block = re.search(r"function updatePriceDisplays\(\) \{.*?\n\}", src, re.S)
        assert block, "找不到 updatePriceDisplays"
        body = block.group(0)
        assert "priceKey === 'premium'" in body, (
            "updatePriceDisplays 需跳過 premium/premium_yearly——"
            "訂閱價是 USD 錨定（USDC on Base），由 PremiumManager 負責"
        )
        assert re.search(r"priceKey === 'premium'\s*\|\|\s*priceKey === 'premium_yearly'", body)

    def test_premium_manager_renders_usd_anchor(self):
        src = _read("web/js/premium.js")
        block = re.search(r"updatePriceDisplay\(\) \{.*?\n    \}", src, re.S)
        assert block, "找不到 updatePriceDisplay"
        body = block.group(0)
        assert 'querySelectorAll(\'[data-price="premium"]\')' in body
        assert "$" in body and "toFixed(2)" in body, "USD 錨定價格式（$12.00）"


class TestTonPricesSubscriptionKeysUnconsumed:
    """鎖定前提：TonPrices.premium* 除了 forum-config 自身外無消費者。"""

    def test_no_external_consumer_of_ton_premium_price(self):
        offenders = []
        for p in (REPO / "web" / "js").rglob("*.js"):
            if p.name == "forum-config.js":
                continue
            src = p.read_text(encoding="utf-8")
            if re.search(r"TonPrices\.premium|getPrice\(['\"]premium", src):
                offenders.append(str(p.relative_to(REPO)))
        assert not offenders, f"TonPrices.premium 出現新消費者：{offenders}"
