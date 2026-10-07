"""E2E guard: admin 使用者彈窗在手機上捲到底，叉叉仍要在畫面內且 tap 得到。

2026-09-11 DANNY 回報：admin 板塊的視窗在手機上太大，「點開後不好按叉叉關掉」。
根因：彈窗整塊是同一個捲動容器，叉叉在內文最頂端、只有 32px；內容比視窗高
（使用者詳情 ≈ 815px vs 可視 ≈ 530px）時往下捲去看 Actions 叉叉就被捲走。

修法：標題列（含叉叉）固定不捲、內文獨立捲動、叉叉放大到 44px 觸控目標，
載入中／載入失敗兩個狀態也都給叉叉。本檔用小手機視窗實際開彈窗、捲到底、
量叉叉位置與大小，再用觸控 tap 把它關掉。
"""

from __future__ import annotations

import importlib.util
import json
from urllib.parse import urlsplit

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
SMALL_PHONE = {"width": 375, "height": 667}
MIN_TAP_TARGET = 44

ADMIN_ROLE_INIT = """
(() => {
    const u = JSON.parse(localStorage.getItem('ton_user') || '{}');
    u.role = 'admin';
    localStorage.setItem('ton_user', JSON.stringify(u));
})();
"""

USER_DETAIL = {
    "user_id": "user-002",
    "username": "Trader2",
    "role": "user",
    "membership_tier": "free",
    "membership_expires_at": "2026-12-01T00:00:00Z",
    "is_active": True,
    "created_at": "2026-08-03T10:00:00Z",
    "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
    "chat_message_count": 128,
    "memory_count": 7,
    "custom_skill_count": 2,
    "recent_audit": [
        {
            "success": i % 3 != 0,
            "action": f"chat.send#{i}",
            "at": "2026-09-10T08:00:00Z",
        }
        for i in range(6)
    ],
}


async def _admin_routes(route):
    path = urlsplit(route.request.url).path
    if path.startswith("/api/admin/users/"):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"user": USER_DETAIL}),
        )
        return
    if path == "/api/admin/users":
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"users": [USER_DETAIL], "total": 1}),
        )
        return
    await route.fallback()


@pytest.mark.e2e
async def test_close_button_stays_reachable_after_scrolling_to_bottom(page):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    await page.add_init_script(ADMIN_ROLE_INIT)
    await page.route("**/api/admin/**", _admin_routes)
    await page.set_viewport_size(SMALL_PHONE)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)

    await page.evaluate("switchTab('admin')")
    await page.wait_for_selector(
        "#admin-subpage-content", state="attached", timeout=10000
    )
    await page.evaluate("AdminPanel.switchSubPage('users')")
    await page.evaluate("AdminPanel.UserManager.openUserModal('user-002')")
    await page.wait_for_selector(
        "#admin-user-modal-body", state="attached", timeout=10000
    )
    await page.wait_for_timeout(300)

    result = await page.evaluate(
        """async () => {
            const modal = document.getElementById('admin-user-modal');
            const body = document.getElementById('admin-user-modal-body');
            const scrollable = body.scrollHeight > body.clientHeight;
            body.scrollTop = body.scrollHeight;  // 捲到底：看 Actions 的位置
            await new Promise(r => setTimeout(r, 100));

            const btn = modal.querySelector('[data-click="closeModal"]');
            const r = btn.getBoundingClientRect();
            const inViewport = r.top >= 0 && r.bottom <= window.innerHeight
                && r.left >= 0 && r.right <= window.innerWidth;
            // 叉叉上面沒有別的東西蓋住（真的點得到）
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            const hittable = !!hit && (hit === btn || btn.contains(hit));

            // 用觸控 tap（pointerdown + pointerup）關閉——走 click-delegator 的觸控路徑
            const init = {
                bubbles: true, cancelable: true, composed: true,
                pointerId: 1, pointerType: 'touch', isPrimary: true, button: 0,
                clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
            };
            btn.dispatchEvent(new PointerEvent('pointerdown', init));
            btn.dispatchEvent(new PointerEvent('pointerup', init));
            await new Promise(r => setTimeout(r, 50));

            return {
                scrollable, inViewport, hittable,
                width: r.width, height: r.height,
                closed: modal.classList.contains('hidden'),
            };
        }"""
    )

    assert result["scrollable"], "測試資料沒讓內文超出視窗，守不到「捲走叉叉」的情境"
    assert result["inViewport"], "捲到底後叉叉跑出畫面外：標題列沒有固定住"
    assert result["hittable"], "叉叉被別的元素蓋住，點不到"
    assert result["width"] >= MIN_TAP_TARGET and result["height"] >= MIN_TAP_TARGET, (
        f"叉叉觸控目標 {result['width']}x{result['height']} 小於 {MIN_TAP_TARGET}px"
    )
    assert result["closed"], "觸控 tap 叉叉沒有把彈窗關掉"


@pytest.mark.e2e
async def test_membership_action_gives_feedback_and_keeps_position(page):
    """2026-09-26 DANNY 回報「點會員一直沒反應」：按下後整個視窗清成轉圈、重畫後
    跳回頂端，剛按的按鈕捲出畫面；以為沒成功再按一次，其實又改回去。
    守：按下立刻鎖住並顯示轉圈、連點只送一次、完成後就地更新且不跳回頂端。"""
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    state = {"tier": "free", "puts": 0}

    async def routes(route):
        req = route.request
        path = urlsplit(req.url).path
        if path.endswith("/membership") and req.method == "PUT":
            state["puts"] += 1
            await page.wait_for_timeout(600)  # 模擬慢一點的伺服器
            state["tier"] = json.loads(req.post_data or "{}").get("tier", "free")
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "tier": state["tier"]}),
            )
            return
        if path.startswith("/api/admin/users/"):
            user = dict(USER_DETAIL, membership_tier=state["tier"])
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"user": user}),
            )
            return
        await _admin_routes(route)

    await page.add_init_script(ADMIN_ROLE_INIT)
    await page.route("**/api/admin/**", routes)
    await page.set_viewport_size(SMALL_PHONE)
    await page.goto(BASE_URL)
    await page.wait_for_selector("#chat-messages", state="attached")
    await page.wait_for_timeout(800)
    await page.evaluate("switchTab('admin')")
    await page.wait_for_selector(
        "#admin-subpage-content", state="attached", timeout=10000
    )
    await page.evaluate("AdminPanel.switchSubPage('users')")
    await page.evaluate("AdminPanel.UserManager.openUserModal('user-002')")
    await page.wait_for_selector(
        "#admin-user-modal-body", state="attached", timeout=10000
    )
    await page.wait_for_timeout(300)

    sel = "[data-click='AdminPanel.UserManager.setMembership']"
    during = await page.evaluate(
        """async (sel) => {
            const body = document.getElementById('admin-user-modal-body');
            body.scrollTop = body.scrollHeight;
            await new Promise(r => setTimeout(r, 50));
            const tap = (el) => {
                const r = el.getBoundingClientRect();
                const init = {
                    bubbles: true, cancelable: true, composed: true,
                    pointerId: 1, pointerType: 'touch', isPrimary: true, button: 0,
                    clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
                };
                el.dispatchEvent(new PointerEvent('pointerdown', init));
                el.dispatchEvent(new PointerEvent('pointerup', init));
            };
            const btn = document.querySelector('#admin-user-modal-content ' + sel);
            tap(btn);
            await new Promise(r => setTimeout(r, 30));
            const busy = btn.getAttribute('aria-busy') === 'true'
                && !!btn.querySelector('.admin-action-spinner');
            tap(btn);  // 連點
            return { busy, scroll: body.scrollTop };
        }""",
        sel,
    )
    await page.wait_for_timeout(1500)
    after = await page.evaluate(
        """(sel) => {
            const body = document.getElementById('admin-user-modal-body');
            const btn = document.querySelector('#admin-user-modal-content ' + sel);
            return { scroll: body.scrollTop, label: btn.innerText };
        }""",
        sel,
    )

    assert during["busy"], "按下後按鈕沒有立刻進入處理中（沒回饋，看起來像沒反應）"
    assert during["scroll"] > 0, "測試前提：視窗要先捲下去"
    assert state["puts"] == 1, f"連點送出了 {state['puts']} 次（會把升級又改回去）"
    assert "Remove Pro" in after["label"], "完成後按鈕沒有就地更新成新狀態"
    assert after["scroll"] > 0, "完成後視窗跳回頂端，剛按的按鈕被捲出畫面"
