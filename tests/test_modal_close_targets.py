"""彈窗關閉鈕的觸控目標（2026-09-11 盤查）。

手機上 32px 以下的叉叉很難按準（DANNY 回報 admin 彈窗「不好按叉叉」）。
盤查發現同一批：各市場選股彈窗的叉叉是 16px 圖示、按鈕沒內距；index.html
的篩選／新聞／警示／工具設定關閉鈕 32px；EVM 錢包引導面板叉叉 ~32px；
Wallet Monitor 勾選框 16px。這裡靜態守住 class，實機尺寸由
tests/e2e/test_mobile_tap_targets.py 量。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WEB = Path(__file__).resolve().parents[1] / "web"
MARKET_FILES = [
    "krstock",
    "jpstock",
    "instock",
    "astock",
    "forex",
    "commodity",
]


@pytest.mark.parametrize("name", MARKET_FILES)
def test_market_picker_close_button_is_44px(name):
    src = (WEB / "js" / f"{name}.js").read_text(encoding="utf-8")
    m = re.search(r'data-click-arg="[a-z]+-picker-modal" class="([^"]*)"', src)
    assert m, f"{name}.js 找不到選股彈窗的關閉鈕"
    cls = m.group(1)
    assert "w-11" in cls and "h-11" in cls, f"{name}.js 選股彈窗叉叉不是 44px：{cls}"


def test_index_html_has_no_32px_round_close_buttons():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert (
        "w-8 h-8 rounded-full bg-background flex items-center justify-center text-textMuted"
        not in html
    ), "index.html 還有 32px 的圓形關閉鈕（篩選／新聞／警示／工具設定）"


def test_evm_guide_cancel_is_44px():
    js = (WEB / "js" / "evm-auth.js").read_text(encoding="utf-8")
    m = re.search(r'id="evm-guide-cancel"[^>]*\n\s*class="([^"]*)"', js)
    assert m, "找不到 EVM 引導面板的叉叉"
    assert "w-11" in m.group(1) and "h-11" in m.group(1), m.group(1)


def test_wallet_monitor_checkbox_labels_have_tap_area():
    js = (WEB / "js" / "walletMonitorTab.js").read_text(encoding="utf-8")
    assert 'inline-flex items-center cursor-pointer">' not in js, (
        "Wallet Monitor 的勾選框 label 只包住 16px 的 input，手機點不到"
    )
    assert "w-3.5 h-3.5 accent-primary" not in js


def test_journal_header_wraps_on_narrow_screens():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    i = html.index('id="journal-add-btn"')
    header = html[html.rfind("<!-- Header -->", 0, i) : i]
    assert "flex-wrap" in header, "Journal 標題列在 375px 會把 Add Entry 擠出畫面"
