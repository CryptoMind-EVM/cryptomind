"""E2E：帳本投資視圖的行事曆區塊（每日早報 Phase B）在瀏覽器裡真的渲染。

conftest 已把使用者種進 localStorage、所有 /api/* 回 {}，所以行事曆會走空狀態；
守的是：區塊存在、切到投資視圖後看得到、表單與新增鈕都在、按新增不會炸。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


@pytest.mark.e2e
async def test_journal_calendar_section_renders(page):
    _requires_playwright()
    await page.goto(BASE_URL + "#journal", wait_until="domcontentloaded")
    await page.wait_for_selector("#journal-tab:not(.hidden)", timeout=15_000)

    # 切到投資視圖（行事曆住在這裡）
    toggle = page.locator('.journal-view-btn[data-click-arg="invest"]')
    await toggle.first.wait_for(state="visible", timeout=15_000)
    await toggle.first.click()
    await page.wait_for_selector("#journal-invest-view:not(.hidden)", timeout=15_000)

    calendar = page.locator("#journal-calendar")
    await calendar.wait_for(state="visible", timeout=15_000)
    # 空狀態（stub 回 {} → events 空）而不是「Loading...」卡住
    await page.wait_for_function(
        "() => { const el = document.querySelector('#journal-calendar-list'); "
        "return el && !/Loading|載入中/.test(el.textContent || ''); }",
        timeout=15_000,
    )
    assert await page.locator("#journal-calendar-date").is_visible()
    assert await page.locator("#journal-calendar-title").is_visible()
    add_btn = page.locator('[data-click="Journal.addCalendarEvent"]')
    assert await add_btn.is_visible()

    # 沒填就按 → 只提示，不噴錯
    await add_btn.click()
    await page.wait_for_function(
        "() => (document.querySelector('#journal-calendar-status')?.textContent || '').trim().length > 0",
        timeout=5_000,
    )
    # 填了再按 → 走 POST（stub 回 {}），狀態文字更新且頁面沒有未捕捉例外
    await page.fill("#journal-calendar-date", "2030-01-15")
    await page.fill("#journal-calendar-title", "E2E reminder")
    await add_btn.click()
    await page.wait_for_timeout(500)
    status = (await page.locator("#journal-calendar-status").text_content()) or ""
    assert status.strip(), "新增後應顯示狀態文字"


@pytest.mark.e2e
async def test_journal_calendar_edit_recurring_event(page):
    """重複事件：清單顯示重複徽章、只有自訂事件有編輯鈕；編輯帶回錨點日並送 PUT。"""
    _requires_playwright()
    import json
    from datetime import date

    today = date.today()
    iso = today.isoformat()
    events = [
        {
            "id": 11,
            "event_date": iso,
            "title": "Card bill",
            "kind": "custom",
            "source": "user",
            "remind_days_before": 2,
            "note": "",
            "recurrence": "monthly",
            "recurrence_until": None,
            "series_start": f"{today.year - 1}-{today.month:02d}-{min(today.day, 28):02d}",
        },
        {
            "id": 12,
            "event_date": iso,
            "title": "NVDA earnings",
            "kind": "earnings",
            "source": "system",
            "remind_days_before": 1,
            "note": "",
            "recurrence": "none",
            "recurrence_until": None,
            "series_start": iso,
        },
    ]
    sent = []

    async def calendar_route(route):
        req = route.request
        if req.method == "GET":
            body = {"success": True, "events": events, "count": len(events)}
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )
            return
        sent.append((req.method, req.url, req.post_data))
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"success": True, "event": events[0]}),
        )

    # 比 conftest 的通用 stub 晚註冊＝優先
    await page.route("**/api/journal/calendar**", calendar_route)
    await page.goto(BASE_URL + "#journal", wait_until="domcontentloaded")
    await page.wait_for_selector("#journal-tab:not(.hidden)", timeout=15_000)
    toggle = page.locator('.journal-view-btn[data-click-arg="invest"]')
    await toggle.first.wait_for(state="visible", timeout=15_000)
    await toggle.first.click()
    await page.wait_for_function(
        "() => (document.querySelector('#journal-calendar-list')?.textContent || '').includes('Card bill')",
        timeout=15_000,
    )

    edit_btns = page.locator(
        '#journal-calendar-list [data-click="Journal.editCalendarEvent"]'
    )
    assert await edit_btns.count() == 1, "系統事件不該有編輯鈕"
    assert (
        await page.locator(
            '#journal-calendar-list [data-lucide="repeat"], #journal-calendar-list svg.lucide-repeat'
        ).count()
        == 1
    )

    await edit_btns.first.click()
    assert await page.locator("#journal-calendar-cancel").is_visible()
    assert await page.input_value("#journal-calendar-title") == "Card bill"
    assert await page.input_value("#journal-calendar-repeat") == "monthly"
    assert (
        await page.input_value("#journal-calendar-date") == events[0]["series_start"]
    ), "要帶錨點日"

    await page.fill("#journal-calendar-title", "Credit card bill")
    await page.locator('[data-click="Journal.addCalendarEvent"]').click()
    await page.wait_for_function(
        "() => document.querySelector('#journal-calendar-cancel')?.classList.contains('hidden')",
        timeout=5_000,
    )
    puts = [s for s in sent if s[0] == "PUT"]
    assert puts and puts[0][1].endswith("/api/journal/calendar/11")
    payload = json.loads(puts[0][2])
    assert payload["title"] == "Credit card bill" and payload["recurrence"] == "monthly"
    assert payload["remind_days_before"] == 2, "沒動到的欄位照原本送回"
