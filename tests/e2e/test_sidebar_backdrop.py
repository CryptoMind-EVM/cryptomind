"""E2E guards: 手機版關閉側邊欄時不可留下孤兒遮罩。

#sidebar-backdrop 是 `fixed inset-0 bg-black/50 backdrop-blur-sm` 的滿版遮罩,
只有 toggleSidebar() 會把它藏起來。switchSession() 與 createNewChat() 曾經
直接對 #chat-sidebar 加 `-translate-x-full` 關閉側邊欄、沒有動遮罩,結果選完
對話歷史後畫面上留著一層半透明黑幕,聊天記錄在後面透出來;點它又會呼叫
toggleSidebar,而此時側邊欄已是關閉狀態,於是側邊欄再次滑出來。
"""

from __future__ import annotations

import importlib.util
import json

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE_VIEWPORT = {"width": 375, "height": 812}

_SESSIONS = {
    "sessions": [
        {"id": "sess-a", "title": "第一則對話", "is_pinned": False},
        {"id": "sess-b", "title": "第二則對話", "is_pinned": False},
    ]
}
_HISTORY = {"history": [], "has_more": False}


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


async def _json_route(route, payload):
    await route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload)
    )


async def _open_chat_with_sessions(page):
    # 後註冊的 route 優先,蓋掉 conftest 的空清單 stub
    await page.route("**/api/chat/sessions*", lambda r: _json_route(r, _SESSIONS))
    await page.route("**/api/chat/history*", lambda r: _json_route(r, _HISTORY))

    await page.set_viewport_size(MOBILE_VIEWPORT)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#sidebar-backdrop", state="attached")
    await page.wait_for_timeout(900)


async def _backdrop_visible(page) -> bool:
    return await page.evaluate(
        """() => {
            const b = document.getElementById('sidebar-backdrop');
            return getComputedStyle(b).display !== 'none';
        }"""
    )


@pytest.mark.e2e
async def test_switch_session_clears_sidebar_backdrop(page):
    """點選對話歷史後,遮罩必須跟著側邊欄一起收掉。"""
    _requires_playwright()
    await _open_chat_with_sessions(page)

    await page.evaluate("() => window.toggleSidebar()")
    await page.wait_for_timeout(400)
    assert await _backdrop_visible(page), "前置條件不成立:開啟側邊欄後遮罩應該要顯示"

    await page.evaluate("() => window.switchSession('sess-b')")
    await page.wait_for_timeout(800)

    assert not await _backdrop_visible(page), (
        "選完對話歷史後 #sidebar-backdrop 還留在畫面上 — "
        "使用者會看到一層半透明黑幕蓋住聊天記錄"
    )


@pytest.mark.e2e
async def test_create_new_chat_clears_sidebar_backdrop(page):
    """開新對話後,遮罩必須跟著側邊欄一起收掉。"""
    _requires_playwright()
    await _open_chat_with_sessions(page)

    # 先讓 currentSessionId 有值,createNewChat 才會走完整流程
    await page.evaluate("() => window.switchSession('sess-a')")
    await page.wait_for_timeout(500)

    await page.evaluate("() => window.toggleSidebar()")
    await page.wait_for_timeout(400)
    assert await _backdrop_visible(page), "前置條件不成立:開啟側邊欄後遮罩應該要顯示"

    await page.evaluate("() => window.createNewChat()")
    await page.wait_for_timeout(800)

    assert not await _backdrop_visible(page), (
        "開新對話後 #sidebar-backdrop 還留在畫面上"
    )
