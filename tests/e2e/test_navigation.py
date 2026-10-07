"""E2E tests for sidebar navigation, tab switching, and SPA routing."""

from __future__ import annotations

import importlib.util

import pytest

from tests.e2e.pages.base_page import BasePage

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


BASE_URL = "http://127.0.0.1:8770/static/index.html"

# Tab IDs that exist in the SPA
SPA_TABS = [
    ("chat", "#chat-tab"),
    ("crypto", "#crypto-tab"),
    ("twstock", "#twstock-tab"),
    ("usstock", "#usstock-tab"),
    ("wallet", "#wallet-tab"),
    ("commodity", "#commodity-tab"),
    ("forex", "#forex-tab"),
    ("settings", "#settings-tab"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_sidebar_visible_on_chat_tab(page):
    """The chat sidebar should be visible when the chat tab is active."""
    _requires_playwright()

    base = BasePage(page, BASE_URL)
    await base.goto(fragment="chat")
    await page.wait_for_timeout(3000)

    assert await base.is_sidebar_visible(), "Sidebar should be visible on chat tab"


@pytest.mark.e2e
async def test_sidebar_visible_on_market_tabs(page):
    """Sidebar stays visible on market tabs.

    2026-08-21（9704bde）行為翻轉：sidebar 現在包含主要導覽（journal/
    crypto/twstock…），所有分頁都必須顯示——舊測試斷言「市場分頁隱藏」
    是 tabsWithSidebar=['chat'] 時代的過期契約。
    """
    _requires_playwright()

    base = BasePage(page, BASE_URL)

    for tab_name in ("crypto", "twstock", "usstock"):
        await base.goto(fragment=tab_name)
        await page.wait_for_timeout(2000)
        assert await base.is_sidebar_visible(), (
            f"Sidebar should be visible on '{tab_name}' tab (site-wide nav)"
        )


@pytest.mark.e2e
async def test_sidebar_persists_across_tab_navigation(page):
    """Sidebar persists across tab switches (site-wide nav since 2026-08-21)."""
    _requires_playwright()

    base = BasePage(page, BASE_URL)
    await base.goto(fragment="crypto")
    await page.wait_for_timeout(2000)
    assert await base.is_sidebar_visible()

    await base.goto(fragment="chat")
    await page.wait_for_timeout(2000)
    assert await base.is_sidebar_visible(), "Sidebar should stay visible on chat tab"


@pytest.mark.e2e
async def test_switch_tab_via_js_function(page):
    """switchTab('settings') should hide chat tab and show settings tab."""
    _requires_playwright()

    base = BasePage(page, BASE_URL)
    await base.goto(fragment="chat")
    await page.wait_for_timeout(2000)

    # Verify chat tab is visible initially
    assert await base.is_tab_visible("chat-tab"), "Chat tab should be visible"

    # Switch via JS
    await base.switch_tab("settings")
    await page.wait_for_timeout(1500)

    assert not await base.is_tab_visible("chat-tab"), "Chat tab should be hidden"
    assert await base.is_tab_visible("settings-tab"), "Settings tab should be visible"


@pytest.mark.e2e
async def test_all_spa_tabs_exist_in_dom(page):
    """All major SPA tabs should exist as DOM elements."""
    _requires_playwright()

    base = BasePage(page, BASE_URL)
    await base.goto(fragment="chat")
    await page.wait_for_timeout(2000)

    for tab_name, selector in SPA_TABS:
        el = await page.query_selector(selector)
        assert el is not None, f"Tab element '{selector}' should exist in DOM"
