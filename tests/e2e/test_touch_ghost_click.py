"""觸控 tap 的「幽靈 click」回歸測試（2026-09-26 DANNY 錄影回報）。

後台在手機上點使用者，詳細視窗轉一下圈就自己關掉。成因：click-delegator
在 pointerup 就合成 click 打開彈窗，同一次 tap 的原生 click 隨後落在剛蓋上
來的背景上；彈窗自己掛的「點背景關閉」（modal.onclick，不經委派）收到它就
關了。論壇文章頁、確認對話框、Telegram 綁定等彈窗都是同一種寫法。

用真的 Chromium 觸控事件重現：點畫面下方的列（彈窗框蓋不到的位置）。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]

PAGE = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { margin: 0; font: 16px sans-serif; }
  .row { height: 60px; margin: 8px; background: #ddd; }
  #modal { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex;
           align-items: center; justify-content: center; }
  #modal.hidden { display: none; }
  #box { width: 80%; height: 120px; background: #fff; }
</style></head><body>
  <div id="list"></div>
  <div id="modal" class="hidden"><div id="box">
    <button id="close" data-click="closeModal" data-click-arg="modal">x</button>
  </div></div>
</body></html>
"""

SETUP = """
() => {
  const list = document.getElementById('list');
  for (let i = 0; i < 10; i++) {
    const row = document.createElement('div');
    row.className = 'row';
    row.id = 'row' + i;
    row.setAttribute('data-click', 'AdminPanel.UserManager.openUserModal');
    list.appendChild(row);
  }
  // 跟 admin.js 的 openUserModal 一樣：同步打開、掛「點背景（不是框本身）就關閉」
  window.AdminPanel = { UserManager: { openUserModal() {
    const modal = document.getElementById('modal');
    modal.classList.remove('hidden');
    modal.onclick = (e) => { if (e.target === modal) modal.classList.add('hidden'); };
  } } };
  window.closeModal = (id) => document.getElementById(id).classList.add('hidden');
}
"""


async def _modal_hidden(page):
    return await page.evaluate(
        "() => document.getElementById('modal').classList.contains('hidden')"
    )


async def test_touch_tap_opened_modal_survives_its_own_click(browser):
    ctx = await browser.new_context(
        viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True
    )
    page = await ctx.new_page()
    await page.set_content(PAGE)
    await page.add_script_tag(path=str(ROOT / "web/js/click-delegator.js"))
    await page.evaluate(SETUP)

    # 第 9 列在畫面下方，彈窗框（置中、120px 高）蓋不到 → 原生 click 會落在背景
    box = await page.locator("#row9").bounding_box()
    assert box["y"] > 915 / 2 + 60 + 20, "測試前提：點的位置要在彈窗框外"
    await page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + 30)
    await page.wait_for_timeout(800)
    assert not await _modal_hidden(page), (
        "tap 打開的彈窗被同一次 tap 的原生 click 關掉了"
    )

    # 之後的新 tap 點背景，仍然要能關閉（只吞同一次 tap 的那個 click）
    await page.touchscreen.tap(20, 20)
    await page.wait_for_timeout(300)
    assert await _modal_hidden(page), "新的一次點背景應該能關閉彈窗"

    # 關閉鈕走委派，也要能用
    await page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + 30)
    await page.wait_for_timeout(800)
    assert not await _modal_hidden(page)
    close = await page.locator("#close").bounding_box()
    await page.touchscreen.tap(close["x"] + 5, close["y"] + 5)
    await page.wait_for_timeout(300)
    assert await _modal_hidden(page)

    await ctx.close()


async def test_mouse_click_unchanged(browser):
    """桌機滑鼠不走觸控路徑：點列開窗、點背景關窗，行為不變。"""
    ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    await page.set_content(PAGE)
    await page.add_script_tag(path=str(ROOT / "web/js/click-delegator.js"))
    await page.evaluate(SETUP)

    await page.click("#row9")
    await page.wait_for_timeout(100)
    assert not await _modal_hidden(page)
    await page.mouse.click(10, 10)
    await page.wait_for_timeout(100)
    assert await _modal_hidden(page)
    await ctx.close()
