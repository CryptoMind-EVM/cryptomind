"""跨分頁守衛:每個分頁捲到底時,最後一個元素都不可被底部輸入框擋住、也不可伸出視窗底。

背景:底部佔位長年是「fixed 元件 + JS 量出來的 padding」,同一個症狀修過四次
(#265~#268)都沒解決實機問題。版面要改成流內排版(flex 保證訊息區底邊等於底部
元件頂邊),這支測試是改造前後的共同基準 —— 改造前記錄哪些分頁本來就紅,改造後
每一頁都必須是綠的。

刻意逐頁跑而不是只看聊天頁:底部佔位的 CSS 是全域的,動它會影響每個分頁。
2026-09-24 手機底部導覽列拔除後,非聊天分頁沒有底部 chrome —— 以視窗底為界,不再略過。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE_VIEWPORT = {"width": 390, "height": 844}

# 與 web/js/spa.js 的 VALID_TABS 同步
ALL_TABS = [
    "chat",
    "board",
    "crypto",
    "twstock",
    "usstock",
    "commodity",
    "forex",
    "hkstock",
    "astock",
    "jpstock",
    "instock",
    "krstock",
    "wallet",
    "friends",
    "forum",
    "settings",
    "admin",
]

# 量一個分頁:顯示它、注入填充內容讓它可捲、捲到底,回報最深的一次遮擋。
#
# 刻意不走 window.switchTab:那條路徑會被登入狀態、NavPreferences 的啟用清單、
# 元件延遲注入擋掉,stub 環境下 18 頁只有 7 頁切得過去 —— 而那是路由的事,不是版面的事。
# 這支測試要守的是**版面不變式**:任何一頁捲到底,最後一個元素都不可以躲在底部元件後面。
# 所以直接顯示分頁 + 自己餵內容,讓每一頁都真的量得到,與有沒有資料/權限無關。
MEASURE_TAB = """
async (tabId) => {
    const tab = document.getElementById(tabId + '-tab');
    if (!tab) return { tab: tabId, skipped: 'no such tab' };

    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    tab.classList.remove('hidden');
    // 分頁的真實內容是延遲注入的,要有它才量得到真的版面
    if (window.Components && typeof window.Components.inject === 'function') {
        try { await window.Components.inject(tabId); } catch (_) {}
    }
    await new Promise(r => setTimeout(r, 400));

    // 底部佔位:(聊天頁的)輸入框;沒有的分頁以視窗底為界
    const bottomChrome = [...document.querySelectorAll(
        '[data-shell-fixed-input]'
    )].filter(el => {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
        const r = el.getBoundingClientRect();
        return r.height > 0 && r.bottom >= window.innerHeight / 2;
    });
    const chromeTop = bottomChrome.length
        ? Math.min(...bottomChrome.map(el => el.getBoundingClientRect().top))
        : window.innerHeight;

    // 這個分頁裡負責捲動的區域(含分頁自己)。不要求它「現在」就有滿出來的內容 ——
    // 內容由我們自己餵,不然沒資料的分頁會靜靜略過,守衛就是空的。
    const scrollers = [tab, ...tab.querySelectorAll('*')].filter(el => {
        const cs = getComputedStyle(el);
        return cs.overflowY === 'auto' || cs.overflowY === 'scroll';
    });
    if (!scrollers.length) return { tab: tabId, skipped: 'no scroll container' };

    // 餵到一定會滿出來為止
    for (const el of scrollers) {
        const filler = document.createElement('div');
        filler.dataset.clearanceFiller = '1';
        for (let i = 0; i < 30; i++) {
            const row = document.createElement('div');
            row.className = 'py-3 px-4 mb-3 rounded-2xl bg-white/5 text-sm';
            row.textContent = tabId + ' 填充列 ' + (i + 1);
            filler.appendChild(row);
        }
        el.appendChild(filler);
    }
    await new Promise(r => setTimeout(r, 200));

    let worst = null;
    for (const el of scrollers) {
        if (el.scrollHeight <= el.clientHeight + 1) continue;   // 餵了還是捲不動:不是捲動區
        el.scrollTop = el.scrollHeight;
        await new Promise(r => requestAnimationFrame(() => r()));
        const last = el.lastElementChild;
        if (!last) continue;
        const covered = Math.round(last.getBoundingClientRect().bottom - chromeTop);
        if (worst === null || covered > worst.covered) {
            worst = { covered, scroller: el.id || el.className.toString().slice(0, 40) };
        }
    }
    // 量完把填充內容清掉,不要污染下一個分頁
    document.querySelectorAll('[data-clearance-filler]').forEach(el => el.remove());

    if (!worst) return { tab: tabId, skipped: 'nothing scrollable even with filler' };
    return { tab: tabId, covered: worst.covered, scroller: worst.scroller,
             chromeTop: Math.round(chromeTop) };
}
"""


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


@pytest.mark.e2e
async def test_every_tab_last_content_clears_bottom_chrome(page):
    """所有分頁:捲到底之後最後一個元素不可伸到底部輸入框底下、也不可伸出視窗底。"""
    _requires_playwright()
    await page.set_viewport_size(MOBILE_VIEWPORT)
    await page.goto(BASE_URL)
    await page.wait_for_selector("[data-shell-fixed-input]", state="attached")
    await page.wait_for_timeout(700)

    results = []
    for tab in ALL_TABS:
        results.append(await page.evaluate(MEASURE_TAB, tab))

    measured = [r for r in results if "covered" in r]
    skipped = [f"{r['tab']}({r['skipped']})" for r in results if "skipped" in r]
    offenders = [r for r in measured if r["covered"] > 1]

    # 印出來:守衛的價值取決於量到幾頁,略過太多就是沒守到
    print(f"\n量到 {len(measured)} 頁:")
    for r in sorted(measured, key=lambda x: -x["covered"]):
        print(f"  {r['tab']:10s} 被蓋住 {r['covered']:+5d}px  ({r['scroller']})")
    print(f"略過 {len(skipped)} 頁: {skipped}")

    assert measured, f"沒有任何分頁量到可捲內容,測試沒有守到東西。skipped={skipped}"
    assert not offenders, (
        "這些分頁捲到底時最後一個元素仍伸到底部元件底下: "
        f"{offenders}(已量測 {len(measured)} 頁,略過 {skipped})"
    )
