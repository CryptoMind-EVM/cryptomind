from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


@pytest.mark.e2e
async def test_safety_is_absent_from_navigation_and_customizer(page):
    _requires_playwright()
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.add_init_script(
        """
        localStorage.setItem('selectedLanguage', 'zh-TW');
        localStorage.setItem('userNavPreferences', JSON.stringify({
            version: 19,
            enabledItems: ['chat', 'crypto', 'twstock', 'usstock', 'safety', 'settings']
        }));
        """
    )
    await page.goto(f"{BASE_URL}#safety")
    await page.wait_for_function("window.FeatureMenu && window.I18n?.isReady()")
    await page.wait_for_url("**#chat")

    assert await page.locator('#sidebar-nav-items [data-tab="safety"]').count() == 0
    assert await page.locator("#safety-tab").count() == 0
    assert await page.locator("#chat-tab").is_visible()
    assert page.url.endswith("#chat")

    await page.evaluate("window.FeatureMenu.open()")
    await page.locator("#feature-menu-modal").wait_for(state="visible")
    assert await page.locator('.feature-menu-item[data-item-id="safety"]').count() == 0
