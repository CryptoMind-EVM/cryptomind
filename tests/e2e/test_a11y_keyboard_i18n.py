"""E2E：鍵盤／語系切換的靜默失效（2026-09-25 a11y／i18n 盤查後守衛）。

1. 全域 Esc：原本找 `.modal-overlay`／`[data-close-modal]`，整站一個都沒有 → Esc 什麼都不關。
   現在點關閉鈕（showConfirm 的 promise 要 resolve），自帶 Esc 的對話框蓋在上面時不動。
2. 手機抽屜鈕 aria-expanded 跟著開合、Esc 關抽屜。
3. languageChanged 一次切換只派發一次（以前 i18next handler＋手動各發一次）。
4. 呼叫端給的確認框標題，語言切換（全頁 data-i18n 重掃）後不會被洗回預設「Confirm Action」。
5. 服務條款切到英文再切回繁中，<a>／<strong> 要回來。
6. governance 頁（沒載 Tailwind）的 toast 看得到。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE = "http://127.0.0.1:8770/static"
MOBILE = {"width": 375, "height": 812}


async def _open(page, viewport=None, lang="zh-TW"):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    await page.add_init_script(f"localStorage.setItem('selectedLanguage', '{lang}');")
    if viewport:
        await page.set_viewport_size(viewport)
    await page.goto(f"{BASE}/index.html")
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_function(
        "typeof window.showConfirm === 'function' && window.I18n?.isReady?.()"
    )
    await page.wait_for_timeout(500)


@pytest.mark.e2e
async def test_esc_cancels_confirm_modal_and_resolves_false(page):
    await _open(page)
    # 用函式形式且不回傳 promise——evaluate 會等回傳的 promise，Esc 之前它永遠不 resolve
    await page.evaluate(
        "() => { window.__confirmResult = 'pending'; showConfirm({ title: 'T', message: 'M' }).then(v => { window.__confirmResult = v; }); }"
    )
    await page.wait_for_selector("#confirm-modal:not(.hidden)")
    await page.keyboard.press("Escape")
    await page.wait_for_function("window.__confirmResult !== 'pending'")
    assert await page.evaluate("window.__confirmResult") is False
    assert await page.evaluate(
        "document.getElementById('confirm-modal').classList.contains('hidden')"
    )


@pytest.mark.e2e
async def test_esc_closes_alert_and_resolves(page):
    await _open(page)
    await page.evaluate(
        "() => { window.__alertDone = false; showAlert({ title: 'T', message: 'M' }).then(() => { window.__alertDone = true; }); }"
    )
    await page.wait_for_selector("#confirm-modal:not(.hidden)")
    await page.keyboard.press("Escape")
    await page.wait_for_function("window.__alertDone === true")
    # showAlert 關閉時把雙按鈕還原——之後的 showConfirm 仍能用 Esc 取消
    await page.evaluate(
        "() => { window.__confirmResult = 'pending'; showConfirm({ title: 'T2' }).then(v => { window.__confirmResult = v; }); }"
    )
    await page.wait_for_selector("#confirm-modal:not(.hidden)")
    await page.keyboard.press("Escape")
    await page.wait_for_function("window.__confirmResult === false")


@pytest.mark.e2e
async def test_esc_closes_plain_modal(page):
    await _open(page)
    await page.evaluate(
        "document.getElementById('global-filter-modal').classList.remove('hidden')"
    )
    await page.keyboard.press("Escape")
    assert await page.evaluate(
        "document.getElementById('global-filter-modal').classList.contains('hidden')"
    )


@pytest.mark.e2e
async def test_esc_leaves_modal_open_when_self_handled_dialog_is_on_top(page):
    """ui-shell 的確認框自己處理 Esc；蓋在 modal 上時，一次 Esc 只能關掉最上面那層。"""
    await _open(page)
    await page.evaluate("""() => {
        document.getElementById('global-filter-modal').classList.remove('hidden');
        window.__dlg = 'pending';
        window.showConfirmDialog({ title: 'x' }).then(v => { window.__dlg = v; });
    }""")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")
    await page.wait_for_function("window.__dlg === false")
    assert not await page.evaluate(
        "document.getElementById('global-filter-modal').classList.contains('hidden')"
    ), "Esc 同時關掉了底下的篩選 modal"
    # 對話框收掉之後，下一次 Esc 才輪到底下那層
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")
    assert await page.evaluate(
        "document.getElementById('global-filter-modal').classList.contains('hidden')"
    )


@pytest.mark.e2e
async def test_esc_closes_highest_modal_first(page):
    """兩個 modal 同時開：先關 z-index 高的那個（帳本表單 z-80 蓋在新聞 z-70 上），
    不是 DOM 順序最後的那個（帳本表單在 DOM 裡比新聞早）。"""
    await _open(page)
    # 先載入帳本模組，Journal.closeForm 才存在
    await page.evaluate("switchTab('journal')")
    await page.wait_for_function("typeof window.Journal?.closeForm === 'function'")
    await page.evaluate("""() => {
        document.getElementById('journal-form-modal').classList.remove('hidden');
        document.getElementById('news-modal').classList.remove('hidden');
    }""")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)
    state = await page.evaluate("""() => ['journal-form-modal', 'news-modal']
        .map(id => document.getElementById(id).classList.contains('hidden'))""")
    assert state == [True, False], f"第一次 Esc 應只關最上層的帳本表單：{state}"
    await page.keyboard.press("Escape")
    assert await page.evaluate(
        "document.getElementById('news-modal').classList.contains('hidden')"
    )


@pytest.mark.e2e
async def test_mobile_drawer_toggle_state_and_esc(page):
    await _open(page, MOBILE)
    toggle = page.locator("#mobile-drawer-toggle")
    assert await toggle.get_attribute("aria-expanded") == "false"
    assert await toggle.get_attribute("aria-label")  # 由 common.menu 翻譯
    await toggle.click()
    await page.wait_for_timeout(300)
    assert await toggle.get_attribute("aria-expanded") == "true"
    assert not await page.evaluate(
        "document.getElementById('sidebar-backdrop').classList.contains('hidden')"
    )
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    assert await toggle.get_attribute("aria-expanded") == "false"
    assert await page.evaluate(
        "document.getElementById('chat-sidebar').classList.contains('-translate-x-full')"
    )


@pytest.mark.e2e
async def test_language_changed_fires_once_per_switch(page):
    await _open(page)
    count = await page.evaluate("""async () => {
        let n = 0;
        const h = () => { n += 1; };
        window.addEventListener('languageChanged', h);
        await window.I18n.changeLanguage('en');
        await new Promise(r => setTimeout(r, 100));
        window.removeEventListener('languageChanged', h);
        return n;
    }""")
    assert count == 1, f"一次切換派發了 {count} 次 languageChanged"
    assert await page.evaluate("localStorage.getItem('selectedLanguage')") == "en"


@pytest.mark.e2e
async def test_dynamic_texts_survive_language_switch(page):
    await _open(page)
    title = await page.evaluate("""async () => {
        showConfirm({ title: 'Delete this entry?', message: 'Cannot undo' });
        await window.I18n.changeLanguage('en');
        await new Promise(r => setTimeout(r, 100));
        return document.getElementById('confirm-modal-title').textContent;
    }""")
    assert title == "Delete this entry?", f"語系重掃把確認框標題洗成：{title!r}"

    status = await page.evaluate("""async () => {
        const s = document.getElementById('auto-refresh-status');
        s.dataset.i18n = 'marketStatus.live';  // 模擬 setAutoRefreshStatus 設成 LIVE 後
        s.textContent = window.I18n.t('marketStatus.live');
        await window.I18n.changeLanguage('zh-TW');
        await new Promise(r => setTimeout(r, 100));
        return s.textContent;
    }""")
    assert status == await page.evaluate("window.I18n.t('marketStatus.live')")


@pytest.mark.e2e
async def test_terms_page_restores_links_after_language_round_trip(page):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    await page.add_init_script("localStorage.setItem('selectedLanguage', 'zh-TW');")
    await page.goto(f"{BASE}/legal/terms-of-service.html")
    await page.wait_for_selector("#legal-lang-select")
    count_js = "document.querySelectorAll('[data-zh] a, [data-zh] strong').length"
    before = await page.evaluate(count_js)
    assert before > 0, "服務條款沒有內嵌連結／粗體——測試前提不成立"
    await page.select_option("#legal-lang-select", "en")
    assert await page.evaluate(count_js) < before  # 英文是純文字譯文
    await page.select_option("#legal-lang-select", "zh-TW")
    after = await page.evaluate(count_js)
    assert after == before, f"切回繁中後內嵌標籤只剩 {after}/{before}"


@pytest.mark.e2e
async def test_governance_toast_is_visible(page):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    await page.set_viewport_size(MOBILE)
    await page.goto(f"{BASE}/governance/index.html")
    await page.wait_for_function("typeof window.showToast === 'function'")
    r = await page.evaluate("""() => {
        window.showToast('hello', 'success');
        const c = document.getElementById('toast-container');
        const t = c.lastElementChild.getBoundingClientRect();
        return { pos: getComputedStyle(c).position, top: t.top, bottom: t.bottom, h: t.height, vh: innerHeight };
    }""")
    assert r["pos"] == "fixed"
    assert r["h"] > 20 and r["top"] >= 0 and r["bottom"] <= r["vh"], (
        f"toast 不在視窗內：{r}"
    )
