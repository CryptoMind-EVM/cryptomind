"""E2E：詐騙檢查分頁 #scamcheck 輸入 0x 地址按 Enter → 真的打健診 API、畫出判定卡。

2026-09-24 正式站：舊列表頁的 i18n 補丁把搜尋覆寫成只認 G 開頭地址，按了沒反應。
2026-09-27：列表頁改成 SPA 分頁（web/js/scam-check.js），同一條路徑改在分頁上驗。
"""

from __future__ import annotations

import importlib.util
import json

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html#scamcheck"
ADDR = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


@pytest.mark.e2e
async def test_search_evm_address_calls_checkup(page):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    hits = []

    async def check_route(route):
        hits.append(route.request.url)
        body = {
            "success": True,
            "address": ADDR,
            "family": "evm",
            "verdict": "no_red_flags",
            "reasons": [],
            "sources": {
                "community": {"status": "ok", "found": False},
                "goplus": {"status": "ok", "flags": [], "has_records": True},
            },
        }
        await route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    await page.route("**/api/scam-tracker/reports/check**", check_route)
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => !!window.ScamCheckTab && !!document.getElementById('scamcheck-input')",
        timeout=15_000,
    )
    await page.fill("#scamcheck-input", f"  {ADDR}  ")
    await page.press("#scamcheck-input", "Enter")
    await page.wait_for_selector('[data-scamcheck-state="clean"]', timeout=10_000)
    assert hits and ADDR in hits[0]
    assert await page.input_value("#scamcheck-input") == ADDR, "前後空白要自動修掉"
