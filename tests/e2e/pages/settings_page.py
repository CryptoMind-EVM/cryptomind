"""Page Object Model for the Settings tab."""

from __future__ import annotations

from playwright.async_api import Page

from tests.e2e.pages.base_page import BasePage


class SettingsPage(BasePage):
    """Encapsulates interactions on the Settings tab."""

    # ------------------------------------------------------------------
    # Selectors
    # ------------------------------------------------------------------
    SETTINGS_TAB = "#settings-tab"

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    async def open(self) -> None:
        """Navigate to the settings tab."""
        await self.goto(fragment="settings")

    async def wait_for_ready(self, timeout: int = 15_000) -> None:
        """Wait until the settings tab is rendered and interactive."""
        await self.page.wait_for_function(
            """() => {
                const tab = document.getElementById('settings-tab');
                return tab && !tab.classList.contains('hidden') && tab.innerText.length > 0;
            }""",
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Language
    # ------------------------------------------------------------------
    async def get_current_language(self) -> str:
        """Return the language stored in localStorage."""
        lang = await self.page.evaluate(
            "() => localStorage.getItem('selectedLanguage')"
        )
        return lang or "zh-TW"

    async def set_language_via_js(self, lang: str) -> None:
        """Change language through I18n.changeLanguage (what the switcher calls).

        原本是自己 dispatch ``languageChanged``——但那是 i18n 的**輸出**事件
        （i18n.js 切完語言才發），沒有任何人監聽它去寫 localStorage。
        自己發等於只是空轉，偏好永遠不會被寫入。
        """
        await self.page.evaluate(
            """async (lang) => {
                await window.I18n.changeLanguage(lang);
            }""",
            arg=lang,
        )

    async def click_language_switcher(self) -> None:
        """Click the language switcher button in the nav bar."""
        await self.page.click(".lang-switcher-container button")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    async def get_html_class(self) -> str:
        """Return the current class on the <html> element (e.g. 'dark' or '')."""
        return await self.page.evaluate("() => document.documentElement.className")

    async def is_dark_theme(self) -> bool:
        """Return True when the dark theme class is present on <html>."""
        cls = await self.get_html_class()
        return "dark" in cls

    # ------------------------------------------------------------------
    # API Key configuration
    # ------------------------------------------------------------------
    # 沿革：先是 modal（`window.openApiKeyModal` / `#apikey-modal`），改版後
    # 內嵌到 Settings 的 AI 區塊，**2026-09-04 起搬到 AI Studio →「模型」分頁**
    # （PR #640；ID 全部沿用，只是換了頁）。所以要先導到 AI Studio。
    async def open_llm_section(self, timeout: int = 15_000) -> None:
        """導到 AI Studio 的「模型」分頁並等它**可見**。

        只等 attached 不夠：元素在 DOM 裡但祖先分頁還沒被 SPA 切出來時，
        Playwright 的 select_option 會卡在「waiting for element to be
        visible and enabled」直到逾時。
        """
        await self.goto(fragment="ai-studio")
        await self.page.wait_for_function(
            "() => window.AIStudioTab && typeof window.AIStudioTab.showSection === 'function'",
            timeout=timeout,
        )
        await self.page.wait_for_selector("#ai-studio-tab", state="visible", timeout=timeout)
        await self.page.evaluate("() => window.AIStudioTab.showSection('models')")
        await self.page.wait_for_selector(
            "#llm-provider-select", state="visible", timeout=timeout
        )

    async def wait_for_llm_section(self, timeout: int = 15_000) -> None:
        """Wait until the LLM binding section is rendered."""
        await self.page.wait_for_selector(
            "#llm-provider-select", state="attached", timeout=timeout
        )

    async def select_llm_provider(self, provider: str) -> None:
        """Pick a provider in the AI settings section (fires llmProviderChange)."""
        await self.page.select_option("#llm-provider-select", provider)

    async def available_llm_providers(self) -> list:
        """Return the provider values offered by the select."""
        return await self.page.evaluate(
            """() => Array.from(
                document.querySelectorAll('#llm-provider-select option')
            ).map((o) => o.value).filter(Boolean)"""
        )

    async def is_api_key_input_enabled(self) -> bool:
        """Return True when the API key input accepts typing."""
        return await self.page.evaluate(
            """() => {
                const input = document.getElementById('llm-api-key-input');
                return !!input && !input.disabled;
            }"""
        )

    # ------------------------------------------------------------------
    # General settings content
    # ------------------------------------------------------------------
    async def get_settings_content_text(self) -> str:
        """Return the inner text of the settings tab."""
        return await self.get_text(self.SETTINGS_TAB)

    async def has_section(self, text: str) -> bool:
        """Return True if a settings section containing *text* exists."""
        content = await self.get_settings_content_text()
        return text in content
