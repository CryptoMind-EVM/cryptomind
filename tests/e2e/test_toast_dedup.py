"""E2E guard: showToast 去重 — 相同訊息短時間內重複呼叫只顯示一個 toast。

線上 #特斯拉設定案例（DANNY 手機回報）：在 AI 設定頁按「Save AI Configuration」
但未先測試 API key 時，「Please complete the API key test before saving.」錯誤
toast 跳了兩個並堆疊在畫面底部。

根因：手機上 pointerdown 委派器合成一個 click，加上原生 click，saveLLMKey 的
驗證失敗 showToast 兩次；而 showToast 原本每次呼叫都建新 DOM 元素、無去重。
修法在 ``web/js/ui-shell.js`` 的 showToast 加 dedup map — 相同 message+tone
在 toast 存活期間重複呼叫只重置計時器、不堆疊新元素。

本檔用真實 browser 驗證：連續呼叫 showToast 同訊息兩次 → toast-container 只有一個元素。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

WEB_JS = Path(__file__).resolve().parents[2] / "web" / "js"


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


def test_showToast_has_dedup_logic():
    """接線檢查：showToast 必須有去重邏輯（dedup map + 相同 key 攔截）。

    沒有去重的話，任何雙觸發（pointerdown 合成 click + 原生 click，或快速連點）
    都會讓相同錯誤訊息堆疊多個 toast。
    """
    source = (WEB_JS / "ui-shell.js").read_text(encoding="utf-8")

    # 必須有 dedup 資料結構
    assert re.search(r"_toastDedup\s*=\s*new Map", source), (
        "ui-shell.js showToast 沒有去重 map：相同訊息重複呼叫會堆疊多個 toast"
        "（線上 #特斯拉設定案例）"
    )
    # 必須在 showToast 裡檢查 existing 並 return（不建新元素）
    assert re.search(r"const existing\s*=\s*_toastDedup\.get\(", source), (
        "showToast 沒有讀取 dedup entry 攔截重複呼叫"
    )


@pytest.mark.asyncio
async def test_showToast_dedup_same_message_single_toast(page):
    """實際 browser 驗證：連續兩次 showToast 同訊息 → 只有一個 toast 元素。"""
    _requires_playwright()
    await page.goto("http://127.0.0.1:8770/static/index.html")
    await page.wait_for_load_state("domcontentloaded")

    # 連續呼叫 showToast 同訊息兩次（模擬 pointerdown + 原生 click 雙觸發）
    count = await page.evaluate(
        """() => {
            if (typeof window.showToast !== 'function') return -1;
            window.showToast('Please complete the API key test before saving.', 'error');
            window.showToast('Please complete the API key test before saving.', 'error');
            const container = document.getElementById('toast-container');
            return container ? container.children.length : -2;
        }"""
    )
    assert count == 1, (
        f"相同訊息連續呼叫兩次應只剩 1 個 toast（去重），實際 {count} 個"
    )


@pytest.mark.asyncio
async def test_showToast_different_messages_both_shown(page):
    """不同訊息不該被去重 — 兩個都該顯示。"""
    _requires_playwright()
    await page.goto("http://127.0.0.1:8770/static/index.html")
    await page.wait_for_load_state("domcontentloaded")

    count = await page.evaluate(
        """() => {
            if (typeof window.showToast !== 'function') return -1;
            window.showToast('第一個錯誤', 'error');
            window.showToast('第二個不同錯誤', 'error');
            const container = document.getElementById('toast-container');
            return container ? container.children.length : -2;
        }"""
    )
    assert count == 2, (
        f"不同訊息應各顯示一個 toast（共 2 個），實際 {count} 個"
    )
