"""E2E guard: Wallet 分頁（交易紀錄）不能依賴使用者先開過 Forum。

2026-09-11 盤查：wallet.js 用 ForumAPI 拉交易資料，但 forum-api.js 只在
Forum 分頁的 lazy loader 裡載入（spa.js）。沒開過 Forum 就進 Wallet →
「Load Error: ForumAPI library not loaded」，手機桌機都會。守：全新頁面直接
切到 wallet 分頁，ForumAPI 要在、console 不能有那句錯誤。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"


@pytest.mark.e2e
async def test_wallet_tab_has_forum_api_without_visiting_forum(page):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)

    await page.evaluate("switchTab('wallet')")
    await page.wait_for_timeout(1500)

    has_api = await page.evaluate("typeof window.ForumAPI !== 'undefined'")
    assert has_api, "切到 wallet 分頁後 ForumAPI 仍未載入：交易紀錄一定會顯示載入失敗"
    offending = [e for e in errors if "ForumAPI library not loaded" in e]
    assert not offending, f"wallet 分頁噴了 ForumAPI 未載入：{offending[:1]}"
