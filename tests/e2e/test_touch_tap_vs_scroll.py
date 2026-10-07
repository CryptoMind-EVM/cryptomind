"""E2E guards: 觸控「輕碰／滑動」不得觸發 [data-click]，只有真正的 tap 才算。

2026-09-11 DANNY 回報：手機上很多按鈕「輕輕碰一下就觸發」，滑動頁面時
手指起點落在按鈕上就會一直誤觸到不想要的功能（全部板塊都會）。

根因：click-delegator 對觸控在 **pointerdown** 就合成 click 派發動作
（為了修「鍵盤收起 → hit-test 落空」），完全沒判斷手指後來有沒有移動——
所以滑動手勢的第一下也被當成點擊。

正確契約（本檔看守）：
  1. 只按下不放（pointerdown）不觸發。
  2. 按下後移動超過 tap slop 再放開（滑動）不觸發。
  3. 瀏覽器接管手勢（pointercancel）不觸發，之後的 pointerup 也不算。
  4. 按下、微幅抖動（< slop）、放開 → 觸發（真正的 tap 要容忍手指抖動）。
  5. tap 之後跟著來的原生 click 要被吞掉，動作只跑一次。
鍵盤修法保留：pointerdown 仍 preventDefault 阻止焦點轉移，見
test_chat_send_pointerdown.py。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE_VIEWPORT = {"width": 375, "height": 812}

# 在頁面裡模擬一段觸控手勢：steps 是 [type, dx, dy] 序列，回傳 sendMessage 被叫的次數。
_GESTURE_JS = """async (steps) => {
    let calls = 0;
    const real = window.sendMessage;
    window.sendMessage = function () { calls += 1; };

    const btn = document.getElementById('send-btn');
    const r = btn.getBoundingClientRect();
    const x0 = r.left + r.width / 2, y0 = r.top + r.height / 2;
    for (const [type, dx, dy] of steps) {
        const Ctor = type === 'click' ? MouseEvent : PointerEvent;
        btn.dispatchEvent(new Ctor(type, {
            bubbles: true, cancelable: true, composed: true,
            pointerId: 7, pointerType: 'touch', isPrimary: true, button: 0,
            clientX: x0 + (dx || 0), clientY: y0 + (dy || 0),
            detail: type === 'click' ? 1 : 0,
        }));
        await new Promise(r => setTimeout(r, 10));
    }
    await new Promise(r => setTimeout(r, 50));
    window.sendMessage = real;
    return calls;
}"""


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


async def _open(page):
    _requires_playwright()
    await page.set_viewport_size(MOBILE_VIEWPORT)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)


@pytest.mark.e2e
async def test_touch_down_alone_does_not_fire(page):
    """手指壓著沒放開，不能觸發——這就是「輕輕碰一下就觸發」的直接症狀。"""
    await _open(page)
    calls = await page.evaluate(_GESTURE_JS, [["pointerdown", 0, 0]])
    assert calls == 0, "pointerdown 還沒放手就派發了動作：滑動起手式會被當成點擊"


@pytest.mark.e2e
async def test_touch_scroll_does_not_fire(page):
    """按下 → 往上滑 40px → 放開：這是捲動，不是點擊。"""
    await _open(page)
    calls = await page.evaluate(
        _GESTURE_JS,
        [
            ["pointerdown", 0, 0],
            ["pointermove", 0, -12],
            ["pointermove", 0, -40],
            ["pointerup", 0, -40],
        ],
    )
    assert calls == 0, "滑動手勢觸發了按鈕：手指移動超過 tap slop 就不該算 tap"


@pytest.mark.e2e
async def test_pointercancel_does_not_fire(page):
    """瀏覽器接管手勢（開始捲動時會發 pointercancel）後，整個手勢作廢。"""
    await _open(page)
    calls = await page.evaluate(
        _GESTURE_JS,
        [["pointerdown", 0, 0], ["pointercancel", 0, 0], ["pointerup", 0, 0]],
    )
    assert calls == 0, "pointercancel 之後仍派發了動作"


@pytest.mark.e2e
async def test_tap_with_jitter_fires_once(page):
    """真正的 tap（按下、手指微抖 4px、放開）要觸發，而且只一次——
    隨後的原生 click 必須被吞掉（雙觸發會讓 toast / 送出跑兩次）。"""
    await _open(page)
    calls = await page.evaluate(
        _GESTURE_JS,
        [
            ["pointerdown", 0, 0],
            ["pointermove", 3, 3],
            ["pointerup", 3, 3],
            ["click", 3, 3],
        ],
    )
    assert calls == 1, f"tap 應觸發恰好一次，實際 {calls} 次"


@pytest.mark.e2e
async def test_tap_fires_before_native_click(page):
    """鍵盤修法的核心不能退：放手（pointerup）當下就派發，不等原生 click。"""
    await _open(page)
    calls = await page.evaluate(
        _GESTURE_JS, [["pointerdown", 0, 0], ["pointerup", 0, 0]]
    )
    assert calls == 1, (
        "pointerup 沒派發動作：又回到只靠原生 click，鍵盤收起會讓第一次點擊落空"
    )


# ---- 以下兩個案例讓 Chromium 自己產生觸控手勢（CDP），不是合成 DOM 事件 ----
# 合成事件測的是「我們的判定邏輯」；這兩個測的是「瀏覽器真的會照這個順序發事件」
# （例如開始捲動時 Chromium 會發 pointercancel 而不是 pointerup）。


async def _touch_cdp(page):
    """conftest 的 context 沒開 hasTouch：用 CDP 打開觸控模擬，Chromium 才會把觸控派成 pointer 事件。"""
    cdp = await page.context.new_cdp_session(page)
    await cdp.send(
        "Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1}
    )
    return cdp


async def _arm_counter(page):
    await page.evaluate(
        """() => {
            window.__calls = 0; window.__events = [];
            window.sendMessage = () => { window.__calls += 1; };
            for (const t of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'click'])
                document.getElementById('send-btn').addEventListener(
                    t, (e) => window.__events.push(t + ':' + (e.pointerType || '')), true);
        }"""
    )
    return await page.evaluate(
        """(() => {
            const r = document.getElementById('send-btn').getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        })()"""
    )


@pytest.mark.e2e
async def test_real_browser_scroll_gesture_does_not_fire(page):
    """手指從按鈕上開始往上滑（Input.synthesizeScrollGesture）：這是捲動，不能觸發。"""
    await _open(page)
    cdp = await _touch_cdp(page)
    box = await _arm_counter(page)
    await cdp.send(
        "Input.synthesizeScrollGesture",
        {
            "x": box["x"],
            "y": box["y"],
            "yDistance": -200,
            "gestureSourceType": "touch",
            "speed": 800,
        },
    )
    await page.wait_for_timeout(500)
    calls, events = await page.evaluate("[window.__calls, window.__events]")
    assert calls == 0, f"真實觸控捲動手勢觸發了按鈕 {calls} 次（事件序列 {events}）"


@pytest.mark.e2e
async def test_real_browser_long_press_fires_once(page):
    """長按 1 秒再放開：只能觸發一次（舊版 pointerdown 派發一次、原生 click 再一次）。"""
    await _open(page)
    cdp = await _touch_cdp(page)
    box = await _arm_counter(page)
    await cdp.send(
        "Input.dispatchTouchEvent",
        {"type": "touchStart", "touchPoints": [{"x": box["x"], "y": box["y"]}]},
    )
    await page.wait_for_timeout(1000)
    await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    await page.wait_for_timeout(300)
    calls, events = await page.evaluate("[window.__calls, window.__events]")
    assert calls == 1, f"長按放開應觸發恰好一次，實際 {calls} 次（事件序列 {events}）"
