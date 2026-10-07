"""E2E: 手機 375px 實量觸控目標與版面（2026-09-11 盤查後守衛）。

1. Journal 分頁不能水平溢出（標題列的 Add Entry 以前被擠出畫面 35px）。
2. Wallet Monitor 警示勾選框的可點區域 ≥ 40px。
3. 圖表工具列（週期／LIVE）≥ 44px（2026-09-25 a11y 盤查）。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE = {"width": 375, "height": 812}


async def _open(page, tab):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    await page.set_viewport_size(MOBILE)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)
    await page.evaluate(f"switchTab('{tab}')")
    await page.wait_for_timeout(1200)


@pytest.mark.e2e
async def test_journal_tab_has_no_horizontal_overflow(page):
    await _open(page, "journal")
    result = await page.evaluate(
        """() => {
            const tab = document.getElementById('journal-tab');
            const btn = document.getElementById('journal-add-btn');
            const r = btn.getBoundingClientRect();
            return { scrollX: tab.scrollWidth - tab.clientWidth, btnRight: r.right, vw: innerWidth };
        }"""
    )
    assert result["scrollX"] == 0, f"journal 分頁可水平捲動 {result['scrollX']}px"
    assert result["btnRight"] <= result["vw"], "Add Entry 按鈕超出視窗"


@pytest.mark.e2e
async def test_wallet_monitor_alert_toggles_are_tappable(page):
    await _open(page, "wallet-monitor")
    sizes = await page.evaluate(
        """() => [...document.querySelectorAll('input[data-alert-key]')].map(i => {
            const r = (i.closest('label') || i).getBoundingClientRect();
            return Math.min(r.width, r.height);
        })"""
    )
    assert sizes, "Wallet Monitor 沒渲染出警示勾選框——測試前提不成立"
    assert min(sizes) >= 40, f"警示勾選框可點區域太小：{sizes}"


# ── 2026-09-25 a11y 盤查：圖表工具列（週期／LIVE）的觸控目標 ──────────────────
# 全域 `button { min-height/min-width: 36px }` 只到 36px，Apple HIG／WCAG AAA 是 44px。
# （市場分頁 index.html 裡的「＋」加自選鈕是死標記：切進分頁時整塊被
#  components/tab-*.js 的模板蓋掉，執行期不存在，所以不在這裡量。）


@pytest.mark.e2e
@pytest.mark.parametrize(
    "section,selector",
    [
        ("chart-section", ".chart-interval-btn, #auto-refresh-btn"),
        ("twstock-chart-section", ".tw-chart-interval-btn"),
        ("usstock-chart-section", ".us-chart-interval-btn"),
    ],
)
async def test_chart_toolbar_buttons_are_44px(page, section, selector):
    await _open(page, "chat")
    sizes = await page.evaluate(
        """([section, selector]) => {
            const el = document.getElementById(section);
            el.classList.remove('hidden');
            return {
                btns: [...el.querySelectorAll(selector)].map(b => {
                    const r = b.getBoundingClientRect();
                    return [b.textContent.trim(), Math.round(r.width), Math.round(r.height)];
                }),
                scrollX: document.documentElement.scrollWidth - innerWidth,
            };
        }""",
        [section, selector],
    )
    assert sizes["btns"], f"{section} 沒有工具列按鈕——測試前提不成立"
    small = [b for b in sizes["btns"] if min(b[1], b[2]) < 44]
    assert not small, f"{section} 觸控目標 < 44px：{small}"
    assert sizes["scrollX"] <= 0, f"{section} 打開後整頁可水平捲動 {sizes['scrollX']}px"
