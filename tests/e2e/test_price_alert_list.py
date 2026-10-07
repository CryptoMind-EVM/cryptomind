"""E2E：台股分頁看得到自己用鈴鐺建立的價格警報（2026-09-25 前端盤查）。

之前 alerts.js 把清單畫進 #alert-list-twstock／#alert-list-usstock，但拆
components.js 時這兩個容器沒搬過去——警報建得出來，卻永遠看不到也刪不掉。
守的是整條接線：分頁模板有容器 → spa 切分頁時呼叫 loadUserAlerts → 只列台股的。
"""

from __future__ import annotations

import importlib.util
import json

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"


@pytest.mark.e2e
async def test_twstock_tab_lists_its_own_alerts(page):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    alerts = {
        "success": True,
        "alerts": [
            {
                "id": "tw-1",
                "symbol": "2330",
                "market": "tw_stock",
                "condition": "above",
                "target": 999,
                "repeat": False,
            },
            {
                "id": "us-1",
                "symbol": "AAPL",
                "market": "us_stock",
                "condition": "below",
                "target": 150,
                "repeat": False,
            },
        ],
    }

    async def alerts_route(route):
        if route.request.method == "GET":
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(alerts)
            )
        else:
            await route.fulfill(status=200, content_type="application/json", body="{}")

    await page.route("**/api/alerts", alerts_route)
    await page.goto(BASE_URL + "#twstock", wait_until="domcontentloaded")
    await page.wait_for_selector(
        "#alert-list-section-twstock:not(.hidden)", timeout=20_000
    )
    listing = page.locator("#alert-list-twstock")
    await listing.get_by_text("2330").wait_for(timeout=10_000)
    text = await listing.inner_text()
    assert "AAPL" not in text, f"台股分頁不該列美股警報：{text}"
    assert (
        await listing.locator(
            '[data-click="deleteUserAlert"][data-click-arg="tw-1"]'
        ).count()
        == 1
    )
