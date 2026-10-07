"""AI Studio 總覽統計磚的排版回歸（2026-09-07 DANNY 回報）。

回報：「排版有點亂掉了，字忽大忽小，版號也被擠掉了」。

成因有兩層：
1. 四塊統計磚各長各的——兩個數字 ``text-2xl``、行動政策也 ``text-2xl``、
   版號卻是 ``text-xs`` 加 ``mt-2``，值列高度與基線全不一致。
2. 行動政策那塊填的是 ``aiStudio.confirmActions``——那個 key 的實際文案是
   **下拉選項的完整句子**（「需要我確認（可用高風險工具）」／
   "Ask me first (high-risk tools allowed)"）。24px 的長句塞進四分之一寬的
   磚，手機上撐成三行，把整排推歪、旁邊的版號擠成一角。

修法：值列統一節奏（固定 h-8 置中），行動政策改吃專屬短標籤
``aiStudio.policyShort*``，長句留在它該在的下拉選項。

本測試守住：短標籤真的短、四塊磚結構一致、renderOverview 不再回頭用長句 key。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "web" / "js" / "components" / "tab-ai-studio.js"
STUDIO_JS = REPO / "web" / "js" / "ai-studio.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")

# 統計磚的值列放得下的極限。中文 4 字／英文一個短詞——超過就是句子，
# 句子屬於下拉選項，不屬於統計磚。
MAX_STAT_VALUE_CHARS = 12

# Catalog 版號 2026-09-08 從總覽磚移除：內部資料版號對使用者沒有意義。
# 值改掛 #ai-studio-overview 的 data-config-version，客服仍查得到。
STAT_VALUE_IDS = (
    "ai-studio-count-profiles",
    "ai-studio-count-presets",
    "ai-studio-current-policy",
)


def _locale(lang: str) -> dict:
    return json.loads((REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8"))


class TestShortPolicyLabels:
    @pytest.mark.parametrize("lang", LOCALES)
    @pytest.mark.parametrize("key", ["policyShortReadOnly", "policyShortConfirm"])
    def test_short_label_exists_and_is_short(self, lang, key):
        value = _locale(lang)["aiStudio"].get(key)
        assert value, f"{lang}: aiStudio.{key} 缺漏"
        assert len(value) <= MAX_STAT_VALUE_CHARS, (
            f"{lang}: aiStudio.{key} = {value!r} 太長，統計磚放不下"
        )

    @pytest.mark.parametrize("lang", LOCALES)
    def test_long_option_sentences_still_exist(self, lang):
        # 長句沒有被改短——它們是下拉選項的文案，那裡需要完整說明
        section = _locale(lang)["aiStudio"]
        for key in ("readOnly", "confirmActions"):
            assert section.get(key), f"{lang}: aiStudio.{key} 不該被移除"

    def test_short_and_long_are_different_strings(self):
        # 兩者相同就代表有人又把長句接回統計磚
        section = _locale("zh-TW")["aiStudio"]
        assert section["policyShortConfirm"] != section["confirmActions"]
        assert section["policyShortReadOnly"] != section["readOnly"]


def _render_overview_body() -> str:
    """取出 AIStudioTab.renderOverview 的函式主體（到下一個同縮排的 `},`）。"""
    src = STUDIO_JS.read_text(encoding="utf-8")
    start = src.index("renderOverview() {")
    end = src.index("\n    },", start)
    return src[start:end]


class TestOverviewRendering:
    def test_policy_tile_uses_short_keys(self):
        body = _render_overview_body()
        assert "aiStudio.policyShortConfirm" in body
        assert "aiStudio.policyShortReadOnly" in body
        # 只看真的取值的地方（註解裡提到 key 名是為了留下脈絡，不算違規）
        looked_up = set(re.findall(r"_t\(\s*'(aiStudio\.[A-Za-z]+)'", body))
        assert "aiStudio.confirmActions" not in looked_up, (
            "統計磚又接回下拉選項的長句 key——那正是排版爆掉的原因"
        )
        assert "aiStudio.readOnly" not in looked_up

    def test_full_policy_kept_in_title(self):
        # 短標籤是給眼睛看的，完整說明不能就這樣消失
        body = _render_overview_body()
        assert "policyEl.title" in body


class TestStatTileMarkup:
    """四塊磚必須共用同一組節奏，否則就會回到「字忽大忽小」。"""

    def _tile_html(self, value_id: str) -> str:
        html = TEMPLATE.read_text(encoding="utf-8")
        idx = html.index(f'id="{value_id}"')
        return html[max(0, idx - 400) : idx + 200]

    @pytest.mark.parametrize("value_id", STAT_VALUE_IDS)
    def test_value_row_has_fixed_height_box(self, value_id):
        # 固定高度的值列＝四塊磚等高、基線對齊
        assert re.search(r'class="flex h-8 items-center justify-center"', self._tile_html(value_id)), (
            f"{value_id} 的值列沒有共用的固定高度容器"
        )

    @pytest.mark.parametrize("value_id", STAT_VALUE_IDS)
    def test_label_row_shares_one_class(self, value_id):
        assert "text-xs leading-snug text-textMuted mt-1.5" in self._tile_html(value_id), (
            f"{value_id} 的標籤列與其他磚不一致"
        )

    def test_counts_use_tabular_nums(self):
        # DESIGN_SYSTEM「數字排版規則」：數字要等寬，否則掃讀時對不齊
        for value_id in ("ai-studio-count-profiles", "ai-studio-count-presets"):
            assert "tabular-nums" in self._tile_html(value_id), f"{value_id} 缺 tabular-nums"

    def test_catalog_version_is_not_a_user_facing_tile(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        assert "ai-studio-config-version" not in html, (
            "Catalog 版號又被做成磚了——那是內部資料版號，使用者看了只會困惑"
        )
        assert "aiStudio.configVersion" not in html

    def test_catalog_version_still_reachable_for_support(self):
        # 不顯示不等於丟掉：客服要問「你的 catalog 哪一版」要查得到
        src = STUDIO_JS.read_text(encoding="utf-8")
        assert "dataset.configVersion" in src

    @pytest.mark.parametrize("value_id", STAT_VALUE_IDS)
    def test_tile_can_shrink(self, value_id):
        # grid 子項預設 min-width:auto，長字串會把整排推寬
        assert "min-w-0 rounded-xl" in self._tile_html(value_id), (
            f"{value_id} 的磚少了 min-w-0，長內容會把整排推歪"
        )
