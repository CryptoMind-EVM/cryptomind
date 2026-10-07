"""E2E tests for Settings tab — language switching, theme, API key modal."""

from __future__ import annotations

import importlib.util

import pytest

from tests.e2e.pages.settings_page import SettingsPage

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


BASE_URL = "http://127.0.0.1:8770/static/index.html"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_settings_tab_opens(page):
    """Navigating to #settings should show the settings tab."""
    _requires_playwright()

    settings = SettingsPage(page, BASE_URL)
    await settings.open()
    await settings.wait_for_ready()

    assert await settings.is_tab_visible("settings-tab"), (
        "Settings tab should be visible"
    )


@pytest.mark.e2e
async def test_language_switch_zh_tw_to_en(page):
    """
    Switching language from zh-TW to en should update visible text
    via the i18n system.
    """
    _requires_playwright()

    settings = SettingsPage(page, BASE_URL)
    await settings.open()
    await settings.wait_for_ready()

    # Ensure we start in Chinese
    assert await settings.get_current_language() in ("zh-TW", None)

    # Switch to English
    await settings.set_language_via_js("en")
    await page.wait_for_timeout(2000)

    # Verify localStorage was updated
    lang = await settings.get_current_language()
    assert lang == "en", f"Expected language 'en', got '{lang}'"


@pytest.mark.e2e
async def test_language_persists_after_reload(page):
    """Language preference should survive a page reload."""
    _requires_playwright()

    settings = SettingsPage(page, BASE_URL)
    await settings.open()
    await settings.wait_for_ready()

    # Set language
    await settings.set_language_via_js("en")
    await page.wait_for_timeout(1000)

    # Reload
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    lang = await settings.get_current_language()
    assert lang == "en", "Language should persist after reload"


@pytest.mark.e2e
async def test_light_theme_is_default(page):
    """預設是淺色——<html> 上不該有 dark class。

    2026-08-25：這條原本斷言「深色是預設」，但 web/styles.css 開頭就寫著
    `:root = 預設淺色（top.co 風格）；html.dark 覆寫深色`——預設在改版時
    翻成淺色，測試沒跟上，於是長期紅著。紅著的 e2e 只會訓練大家忽略它。
    """
    _requires_playwright()

    settings = SettingsPage(page, BASE_URL)
    await settings.open()
    await page.wait_for_timeout(2000)

    assert not await settings.is_dark_theme(), (
        f"預設應為淺色，實際 <html> class = {await settings.get_html_class()!r}"
    )


@pytest.mark.e2e
async def test_selecting_provider_unlocks_api_key_input(page):
    """選好 provider 後，API Key 輸入框要解鎖。

    2026-09-03：這條原本叫 test_api_key_modal_open_and_close，打的是
    ``window.openApiKeyModal()`` / ``#apikey-modal``——兩者在改版時都被拿掉了
    （API key 設定改成 tab-settings.js 內嵌在 Settings 的 AI 區塊），測試沒跟上
    於是長期紅著。改成守目前真正的行為：llmSettings.updateLLMFormState() 會在
    provider 選定後解鎖金鑰輸入（未選模型也能輸入，見該函式 2026-08-28 註解）。
    """
    _requires_playwright()

    settings = SettingsPage(page, BASE_URL)
    # 2026-09-04（PR #640）：金鑰綁定從 Settings 搬到 AI Studio →「模型」分頁。
    # ID 全部沿用，所以底下的斷言不用改，只是入口換了。
    await settings.open_llm_section()

    providers = await settings.available_llm_providers()
    assert providers, "Provider select should offer at least one provider"

    await settings.select_llm_provider(providers[0])
    await page.wait_for_function(
        """() => {
            const input = document.getElementById('llm-api-key-input');
            return !!input && !input.disabled;
        }""",
        timeout=10_000,
    )
    assert await settings.is_api_key_input_enabled(), (
        "API key input should be enabled once a provider is selected"
    )
