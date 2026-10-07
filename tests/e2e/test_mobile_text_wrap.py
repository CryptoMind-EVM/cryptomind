"""E2E: 手機上「一行變兩行」的守衛（2026-09-12 DANNY 回報，全站掃描後修的幾處）。

用 Range API 數文字實際佔的 line box（不是估高度），在 375px 實量：
1. 底部導覽（英文）的標籤不能被截斷（以前「TW Stock」→「TW St…」、
   「Investment Journal」→「Invest…」；改用 nav.short.* 短標籤＋按鈕加寬）。
2. Connections 分頁的狀態徽章必須單行（以前「未連結」被壓成直排三行）。
3. 台股估值卡的「殖利率」標籤必須單行（以前 9px CJK 在 24px 格子裡直排）。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE = {"width": 375, "height": 812}

LINES_JS = """(sel) => [...document.querySelectorAll(sel)].map(el => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const tops = new Set(); let n;
    while ((n = walker.nextNode())) {
        if (!n.nodeValue.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(n);
        for (const r of range.getClientRects()) if (r.width > 0) tops.add(Math.round(r.top));
    }
    return { text: (el.innerText || '').trim(), lines: tops.size, clipped: el.scrollWidth > el.clientWidth + 1 };
})"""


async def _open(page, lang, tab):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    await page.add_init_script(f"localStorage.setItem('selectedLanguage', '{lang}');")
    await page.set_viewport_size(MOBILE)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)
    if tab:
        await page.evaluate(f"switchTab('{tab}')")
        await page.wait_for_timeout(1200)


@pytest.mark.e2e
async def test_connection_status_badges_stay_on_one_line(page):
    await _open(page, "zh-TW", "connections")
    rows = await page.evaluate(LINES_JS, "#telegram-status-badge, #line-status-badge")
    assert rows, "Connections 沒有狀態徽章"
    bad = [r for r in rows if r["lines"] != 1]
    assert not bad, f"狀態徽章不是單行：{bad}"


@pytest.mark.e2e
async def test_twstock_valuation_labels_stay_on_one_line(page):
    await _open(page, "zh-TW", "twstock")
    rows = await page.evaluate(
        LINES_JS, "#twstock-tab .whitespace-nowrap.text-\\[9px\\]"
    )
    assert rows, "台股估值卡的標籤沒渲染（測試前提不成立）"
    bad = [r for r in rows if r["lines"] != 1]
    assert not bad, f"估值標籤變多行：{bad[:3]}"
