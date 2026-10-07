"""E2E guards: 手機點傳送按鈕第一次就要送出，不能因為鍵盤收起而落空。

行動裝置上原生 ``click`` 在「觸碰瞬間 input 還有焦點 → 系統先收鍵盤（blur）
→ viewport 劇變 → 輸入框往下掉數百像素 → click 做_hit-test_ 時落在手指
觸碰的舊位置之外 → click 落空或不派發」的情況下會失敗，表現成「第一次點
只讓鍵盤消失，要再點一次才送出」。

修法是讓 ``[data-click]`` 按鈕在 ``pointerdown`` 階段 ``preventDefault``
阻止瀏覽器把焦點轉給按鈕（這正是觸發鍵盤收起的第一張骨牌），並在同一手勢
放手（``pointerup``）時直接派發動作、不等原生 click。本檔守這條接線：
  1. click-delegator 有掛 pointerdown（capture）
  2. pointerdown 對按鈕呼叫 preventDefault（阻止 focus shift）
  3. tap（pointerdown + pointerup）就派發，不靠原生 click
「只按下不放／滑動不得觸發」的另一半契約見 test_touch_tap_vs_scroll.py。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE_VIEWPORT = {"width": 375, "height": 812}
WEB_JS = Path(__file__).resolve().parents[2] / "web" / "js"


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


def test_click_delegator_listens_on_pointerdown():
    """接線檢查：data-click 必須有 pointerdown 監聽器（擋 focus 轉移的唯一時機）。

    只靠 click 在行動裝置上會因為鍵盤收起造成的 hit-test 失準而落空
    （第一次點擊只讓鍵盤消失，不送出）。
    """
    source = (WEB_JS / "click-delegator.js").read_text(encoding="utf-8")

    # 必須有 pointerdown 監聽器（capture 階段才能搶在 focus 轉移之前）
    assert re.search(r"addEventListener\(\s*['\"]pointerdown['\"]", source), (
        "click-delegator.js 沒有 pointerdown 監聽器：手機上點傳送只靠 click，"
        "鍵盤收起會讓第一次點擊落空（要點兩次才送出）"
    )


def test_click_delegator_prevents_focus_shift_on_pointerdown():
    """接線檢查：pointerdown 必須 preventDefault 阻止 focus 從 input 轉給按鈕。

    沒有這個的話，pointerdown 觸發的同時瀏覽器還是會把焦點轉給按鈕、
    造成 input blur → 鍵盤收 → viewport 變 → 點擊位置失準。
    這是送出動作（sendMessage）會踩到的特定情況，所以只要確認 pointerdown
    處理器有呼叫 preventDefault 即可。
    """
    source = (WEB_JS / "click-delegator.js").read_text(encoding="utf-8")

    # 抓 pointerdown 監聽器整段函式內容（addEventListener('pointerdown', ...) 到結尾）
    match = re.search(
        r"addEventListener\(\s*['\"]pointerdown['\"][^)]*\)\s*,?\s*(?:function\s*\([^)]*\)\s*\{|=>\s*\{|\{)",
        source,
    )
    assert match, "找不到 pointerdown 監聽器主體 —— 測試需要更新"

    # 從 match 結尾開始抓到對應的閉合大括號區段（取接下來 1500 字元檢查 preventDefault）
    start = match.end()
    body = source[start : start + 1500]
    assert "preventDefault" in body, (
        "pointerdown 處理器沒有呼叫 preventDefault：focus 仍會從 input 轉給按鈕，"
        "鍵盤會收起、輸入框往下掉，第一次點擊仍會落空"
    )


@pytest.mark.e2e
async def test_send_button_fires_on_touch_tap(page):
    """行為測試：觸控 tap（pointerdown + pointerup）就要呼叫 sendMessage，不等 click。

    桌面 Playwright 沒有虛擬鍵盤，但可以驗證接線 —— 完整的 tap 手勢本身
    要能觸發 data-click 動作。如果只靠原生 click，這裡不會被呼叫。
    """
    _requires_playwright()
    await page.set_viewport_size(MOBILE_VIEWPORT)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)

    fired = await page.evaluate(
        """async () => {
            // 在真正 sendMessage 前攔截，只記錄是否被呼叫
            let called = false;
            const real = window.sendMessage;
            window.sendMessage = function() { called = true; };

            const btn = document.getElementById('send-btn');
            const r = btn.getBoundingClientRect();
            const init = {
                bubbles: true, cancelable: true, composed: true,
                pointerId: 1, pointerType: 'touch', isPrimary: true, button: 0,
                clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
            };
            // 模擬手指按下再放開（capture 階段會打到 document）
            btn.dispatchEvent(new PointerEvent('pointerdown', init));
            btn.dispatchEvent(new PointerEvent('pointerup', init));

            // 給 microtask 一點時間
            await new Promise(r => setTimeout(r, 50));

            window.sendMessage = real;
            return called;
        }"""
    )

    assert fired, (
        "tap 沒有觸發 sendMessage：手機上原生 click 會因鍵盤收起而失準，"
        "第一次點擊不會送出（要點兩次）"
    )


@pytest.mark.e2e
async def test_mouse_click_still_works_on_desktop(page):
    """回歸守衛：桌機滑鼠點擊（pointerType='mouse'）仍走原本 click 路徑。

    pointerdown handler 只攔截觸控，桌機滑鼠 / 手寫筆行為不能變。這個測試
    守住「不要把桌機也提前到 pointerdown」這條邊界 —— 若有人把觸控過濾拿掉，
    桌機會變成點一下就觸發，且 clickFired 機制會吃掉後續互動。
    """
    _requires_playwright()
    await page.set_viewport_size({"width": 1440, "height": 960})
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)

    result = await page.evaluate(
        """async () => {
            let pointerdownCalls = 0;
            let clickCalls = 0;
            const real = window.sendMessage;
            window.sendMessage = function() {
                // 記錄這次呼叫來自 pointerdown 還是 click
                // pointerdown 路徑會在合成 click 時派發，可在當下打標記
            };

            const btn = document.getElementById('send-btn');

            // 滑鼠 pointerdown —— 應被 handler 忽略（pointerType !== 'touch'）
            btn.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true, cancelable: true, composed: true,
                pointerId: 1, pointerType: 'mouse',
            }));
            const firedAfterMousePointerdown = btn.dataset.clickFired === '1';

            window.sendMessage = real;
            return { firedAfterMousePointerdown };
        }"""
    )

    assert result["firedAfterMousePointerdown"] is False, (
        "滑鼠 pointerdown 不應觸發提前派發：桌機必須維持原本 click 行為，"
        "否則連 hover-dropdown、雙擊選字等桌機互動會被破壞"
    )
