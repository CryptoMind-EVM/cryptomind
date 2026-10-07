"""E2E guards: 串流期間內容長高時不可讓最後一列滑到固定輸入框底下。

分析可以跑數十秒，期間進度列（「正在分析您的請求… 79.3s」）會持續長高。
原本只在送出後 100ms 捲一次到底，之後就不再捲 —— 進度列慢慢滑到固定輸入框
下面被蓋住，使用者只看得到半截。

同時要守住另一半：使用者主動往上捲回頭看訊息時，不可以把他硬拉回底部。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE_VIEWPORT = {"width": 375, "height": 812}
WEB_JS = Path(__file__).resolve().parents[2] / "web" / "js"


def test_analysis_timer_calls_stick_to_bottom():
    """接線檢查：分析計時器每次 tick 都要維持貼底。

    下面兩個行為測試直接呼叫 window.stickChatToBottom()，驗的是 helper 本身；
    如果有人把 chat-analysis.js 裡的呼叫刪掉，那兩個測試仍會通過。這個測試守的
    就是那條接線 —— 進度列會不會被蓋住，取決於計時器有沒有真的呼叫它。
    """
    source = (WEB_JS / "chat-analysis.js").read_text(encoding="utf-8")

    match = re.search(
        r"timerInterval\s*=\s*setInterval\(\s*\(\)\s*=>\s*\{(.*?)\n\s*\},\s*\d+\s*\)",
        source,
        re.S,
    )
    assert match, "找不到分析計時器的 setInterval —— 測試需要更新"

    assert "stickChatToBottom" in match.group(1), (
        "分析計時器沒有呼叫 stickChatToBottom：串流期間內容長高時不會跟著捲動，"
        "進度列會滑到固定輸入框底下被蓋住"
    )


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


async def _open_chat(page):
    await page.set_viewport_size(MOBILE_VIEWPORT)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)


@pytest.mark.e2e
async def test_growing_content_stays_above_fixed_input(page):
    """內容持續長高時，最後一列要保持在輸入框上方。"""
    _requires_playwright()
    await _open_chat(page)

    result = await page.evaluate(
        """async () => {
            const box = document.getElementById('chat-messages');
            box.innerHTML = '';
            window.resetChatStickToBottom();

            // 模擬串流：分批加內容，每批之後像 timer 一樣呼叫 stick
            for (let i = 0; i < 25; i++) {
                const row = document.createElement('div');
                row.className = 'py-3 px-4 mx-4 mb-2 rounded-xl bg-white/5 text-sm';
                row.textContent = '串流內容第 ' + (i + 1) + ' 段';
                box.appendChild(row);
                window.stickChatToBottom();
                await new Promise(r => setTimeout(r, 10));
            }
            await new Promise(r => setTimeout(r, 200));

            const last = box.lastElementChild.getBoundingClientRect();
            const input = document.querySelector('[data-shell-fixed-input]').getBoundingClientRect();
            return { lastBottom: last.bottom, inputTop: input.top };
        }"""
    )

    assert result["lastBottom"] <= result["inputTop"] + 1, (
        "串流最後一列被固定輸入框蓋住："
        f"last.bottom={result['lastBottom']} input.top={result['inputTop']}"
    )


@pytest.mark.e2e
async def test_user_scrolling_up_is_not_yanked_back(page):
    """使用者主動往上捲時，後續內容增長不可把他拉回底部。"""
    _requires_playwright()
    await _open_chat(page)

    result = await page.evaluate(
        """async () => {
            const box = document.getElementById('chat-messages');
            box.innerHTML = '';
            window.resetChatStickToBottom();
            for (let i = 0; i < 30; i++) {
                const row = document.createElement('div');
                row.className = 'py-6 px-4 mx-4 mb-2 rounded-xl bg-white/5';
                row.textContent = '訊息 ' + (i + 1);
                box.appendChild(row);
            }
            window.stickChatToBottom();
            await new Promise(r => setTimeout(r, 100));

            // 使用者往上捲
            box.scrollTop = 0;
            box.dispatchEvent(new Event('scroll'));
            await new Promise(r => setTimeout(r, 100));
            const afterUserScroll = box.scrollTop;

            // 內容繼續長高（模擬串流）
            for (let i = 0; i < 10; i++) {
                const row = document.createElement('div');
                row.className = 'py-6 px-4 mx-4 mb-2 rounded-xl bg-white/5';
                row.textContent = '新增 ' + (i + 1);
                box.appendChild(row);
                window.stickChatToBottom();
            }
            await new Promise(r => setTimeout(r, 150));

            return { afterUserScroll, afterGrowth: box.scrollTop };
        }"""
    )

    assert result["afterGrowth"] == result["afterUserScroll"], (
        "使用者往上捲之後又被拉回底部："
        f"{result['afterUserScroll']} → {result['afterGrowth']}"
    )
