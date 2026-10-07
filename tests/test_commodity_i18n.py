"""商品名稱必須跟隨 UI 語言（2026-09-10 DANNY：UI 切英文，商品仍顯示
「黃金 Gold」中英混合）。

根因：commodity.js 的 AVAILABLE_SYMBOLS／COMMODITY_GROUPS.names 與後端
commodity.py 的 name 都是寫死的中英混合字串，渲染直接用，完全沒走 i18n。
修法：symbol → i18n key 的集中映射（localizedName），四個顯示點全部改用；
i18n 鍵補齊 12 商品＋汽油（四語）。
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


ALL_KEYS = [
    "gold", "silver", "platinum", "copper",
    "wtiCrude", "brentCrude", "naturalGas", "gasoline",
    "wheat", "corn", "soybean", "coffee", "sugar",
]


class TestI18nKeysComplete:
    def test_all_commodity_names_in_every_locale(self):
        for lang in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(_read(f"web/js/i18n/{lang}.json"))
            commodity = data.get("commodity", {})
            missing = [k for k in ALL_KEYS if not commodity.get(k)]
            assert not missing, f"{lang}.json commodity 缺：{missing}"


class TestLocalizedRendering:
    def test_localized_name_helper_exists(self):
        src = _read("web/js/commodity.js")
        assert re.search(r"function localizedName\(|const localizedName =", src), (
            "需要 symbol→i18n 的集中映射 helper"
        )

    def test_watchlist_row_uses_localized_name(self):
        src = _read("web/js/commodity.js")
        m = re.search(r"const known = AVAILABLE_SYMBOLS\.find\(.*?\n.*?const label = (.*?);", src)
        assert m, "找不到 watchlist label 推導"
        assert "localizedName" in m.group(1), "watchlist 列要用在地化名稱"

    def test_manage_dialog_uses_localized_name(self):
        src = _read("web/js/commodity.js")
        assert not re.search(r"\$\{s\.name\}", src), (
            "管理對話框不得直接用寫死的 s.name"
        )
        assert "localizedName(s.symbol)" in src

    def test_pulse_detail_prefers_localized_name(self):
        src = _read("web/js/commodity.js")
        assert re.search(r"\$\{localizedName\(data\.symbol", src), (
            "脈衝詳情的名稱要優先 localizedName（後端 name 只當 fallback）"
        )
        assert not re.search(r"\$\{data\.name \|\| data\.symbol\}", src)

    def test_no_hardcoded_bilingual_names_in_render(self):
        src = _read("web/js/commodity.js")
        # 渲染不得再出現寫死的中英混合名稱（chat-analysis.js 的分頁 regex 除外）
        assert "黃金 Gold" not in src
        assert "WTI原油 Crude Oil" not in src
        assert "'WTI原油'" not in src and "'黃金'" not in src, (
            "COMMODITY_GROUPS.names 的寫死中文名要移除（改走 localizedName）"
        )
