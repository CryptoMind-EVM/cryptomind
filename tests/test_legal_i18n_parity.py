"""Legal 頁四語屬性系統一致性（治理規則，DANNY 2026-08-20）。

legal 頁使用獨立的 data-zh / data-zh-cn / data-en / data-ru 屬性系統
（不進主 i18n JSON——避免大量法律長文灌爆主翻譯檔）。治理規則：
每頁四種屬性數量必須完全一致、值不得為空，否則代表某語言漏譯或
根本沒考慮進去。對應主 JSON 的四語一致性測試見 test_i18n_coverage.py。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

LEGAL_DIR = Path(__file__).resolve().parents[1] / "web" / "legal"
ATTRS = ("data-zh", "data-zh-cn", "data-en", "data-ru")
# 尚未遷移到四語屬性系統的頁面（三頁已於 2026-08-20 全數遷移完成；
# 新增 legal 頁時若尚未四語化，先列入此清單追蹤）。
PENDING_MIGRATION: set[str] = set()


def _migrated_pages() -> list[Path]:
    return [
        p
        for p in sorted(LEGAL_DIR.glob("*.html"))
        if p.name not in PENDING_MIGRATION
    ]


def test_every_legal_page_is_either_migrated_or_tracked():
    """防呆：沒有頁面能「沒遷移也沒被追蹤」，追蹤清單也不能含已遷移/不存在的頁。"""
    pages = {p.name for p in LEGAL_DIR.glob("*.html")}
    untracked = {
        name
        for name in pages - PENDING_MIGRATION
        if 'data-ru="' not in (LEGAL_DIR / name).read_text(encoding="utf-8")
    }
    assert not untracked, (
        f"這些 legal 頁仍未遷移四語系統且未列入 PENDING_MIGRATION 追蹤："
        f"{sorted(untracked)}"
    )
    stale = {n for n in PENDING_MIGRATION if n not in pages}
    assert not stale, f"追蹤清單含不存在的頁面：{sorted(stale)}"


def test_migrated_legal_pages_have_four_lang_parity():
    """四種語言屬性數量必須完全一致（數量不等 = 某語言漏了對應文本）。"""
    for page in _migrated_pages():
        text = page.read_text(encoding="utf-8")
        counts = {a: len(re.findall(a + r'="', text)) for a in ATTRS}
        assert len(set(counts.values())) == 1, (
            f"{page.name} 四語屬性數量不一致：{counts}——每個 data-zh 元素"
            "都必須帶齊 data-zh-cn / data-en / data-ru"
        )


def test_migrated_legal_pages_have_no_empty_lang_attr():
    """空屬性 = 該語言切換時會 fallback 顯示別的語言（舊雙語系統的缺陷）。"""
    for page in _migrated_pages():
        text = page.read_text(encoding="utf-8")
        for a in ATTRS:
            empties = re.findall(a + r'=""', text)
            assert not empties, (
                f"{page.name} 有 {len(empties)} 個空 {a}（該語言漏譯）"
            )


LANG_CODES = ("zh-TW", "zh-CN", "en", "ru")


def test_legal_pages_use_direct_language_select_not_cycle_toggle():
    """語言鈕必須一次點到目標語言（DANNY 2026-09-20）。

    舊版是循環鈕（zh-TW → en → zh-CN → ru），要切到英文得按好幾次、
    按鈕上又只顯示「下一個語言」，使用者根本猜不到現在在哪。改成
    <select> 四語直選後，這裡守住不回潮。
    """
    for page in _migrated_pages():
        text = page.read_text(encoding="utf-8")
        assert 'id="legal-lang-select"' in text, (
            f'{page.name} 缺 <select id="legal-lang-select">（語言直選）'
        )
        for code in LANG_CODES:
            assert f'<option value="{code}"' in text, (
                f"{page.name} 語言選單缺 {code} 選項"
            )
        for legacy in ("toggleLanguage", "LEGAL_LANG_CYCLE", "LEGAL_NEXT_LABEL"):
            assert legacy not in text, (
                f"{page.name} 仍殘留循環切換 {legacy}——語言要直選不循環"
            )
