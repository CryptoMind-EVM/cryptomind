"""E2E：條款 modal 跟著目前語言走四語（真瀏覽器的 DOMParser／cloneNode 路徑）。

邏輯細節在 tests/js/legal_modal_i18n.mjs；這裡確認實際抓 web/legal/*.html、
解析、複製進 modal 之後，zh-CN／ru 使用者看到的是自己的語言（以前是英文）。
"""

from __future__ import annotations

import importlib.util
import json

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    "lang,notice,title",
    [
        ("zh-CN", "重要通知：", "服务条款"),
        ("ru", "ВАЖНОЕ УВЕДОМЛЕНИЕ: ", "Условия обслуживания"),
        ("en", "IMPORTANT NOTICE: ", "Terms of Service"),
    ],
)
async def test_terms_modal_shows_current_language(page, lang, notice, title):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => typeof window.showLegalPage === 'function' && !!window.I18n"
    )
    await page.evaluate(
        f"async () => {{ window.I18n.getLanguage = () => {json.dumps(lang)};"
        " await window.showLegalPage('terms'); }"
    )
    text = await page.evaluate(
        "() => document.getElementById('legal-content').innerText"
    )
    assert notice.strip() in text, f"{lang} 使用者看到的不是自己的語言：{text[:200]!r}"
    if lang != "en":
        assert "IMPORTANT NOTICE" not in text
    assert (
        await page.evaluate("() => document.getElementById('legal-title').textContent")
        == title
    )
