"""i18n 修復測試（Bug#1 + Bug#2）。

Bug#1：get_tool_display_name 支援 4 語，英文 UI 不再顯示中文工具名。
Bug#2：_get_system_prompt 含明確輸出語言指示，LLM 不再無視語言。

覆蓋：
- 成功：4 語各別取正確翻譯
- 拒絕：未知 tool_id / 空字串不洩漏內部 ID
- 邊界：語言正規化（en-US→en, zh-Hans→zh-CN, 未知→zh-TW）
- 向下相容：不傳 language 仍是繁中（舊呼叫端不壞）
- Bug#2：4 語 system prompt 都含輸出語言指示
"""

from __future__ import annotations

import pytest

from core.agents.tool_name_registry import (
    _normalize_lang,
    get_tool_display_name,
)
from core.agents.tool_name_translations import (
    _TRANSLATIONS,
    get_supported_languages,
    get_translation,
)

# ── Bug#1：get_tool_display_name 多語 ──────────────────────────────────────────


class TestGetToolDisplayNameI18n:
    """Bug#1：工具 display_name 4 語支援。"""

    @pytest.mark.parametrize(
        "language,expected",
        [
            ("en", "Real-time Crypto Price"),
            ("zh-TW", "即時加密貨幣價格"),
            ("zh-CN", "即时加密货币价格"),
            ("ru", "Актуальная цена криптовалюты"),
        ],
    )
    def test_returns_correct_language(self, language, expected):
        """4 語各自回正確翻譯。"""
        assert get_tool_display_name("get_crypto_price", language) == expected

    def test_wallet_overview_english(self):
        """回歸：原本觸發 bug 的工具（get_my_wallet_overview）英文正確。"""
        # 2026-09-12 起工具涵蓋 EVM＋TON 綁定錢包，名稱跟著改
        assert (
            get_tool_display_name("get_my_wallet_overview", "en")
            == "Wallet Holdings Overview"
        )

    def test_backward_compatible_no_language(self):
        """向下相容：不傳 language 仍是繁中（舊呼叫端行為不變）。"""
        assert get_tool_display_name("get_crypto_price") == "即時加密貨幣價格"

    def test_unknown_tool_fallback_per_language(self):
        """未知 tool_id 的 fallback 詞也是 4 語。"""
        assert get_tool_display_name("nonexistent_xyz", "en") == "tool"
        assert get_tool_display_name("nonexistent_xyz", "zh-TW") == "工具"
        assert get_tool_display_name("nonexistent_xyz", "ru") == "инструмент"

    def test_empty_tool_id_fallback_per_language(self):
        """空字串 fallback 也是 4 語。"""
        assert get_tool_display_name("", "en") == "tool"
        assert get_tool_display_name("", "zh-CN") == "工具"

    def test_does_not_leak_internal_id_any_language(self):
        """回歸守門：任何語言都不洩漏 snake_case tool_id。"""
        for lang in get_supported_languages():
            display = get_tool_display_name("fetch_url", lang)
            assert "fetch_url" not in display, f"{lang} 洩漏內部 ID: {display}"


class TestNormalizeLang:
    """語言代碼正規化（處理變體）。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("en", "en"),
            ("en-US", "en"),
            ("EN", "en"),
            ("zh-TW", "zh-TW"),
            ("zh-CN", "zh-CN"),
            ("zh-Hans", "zh-CN"),  # 簡體變體 → zh-CN
            ("zh-HK", "zh-TW"),  # 未支援中文變體 → zh-TW
            ("ru", "ru"),
            ("ru-RU", "ru"),
            ("fr", "zh-TW"),  # 未支援 → 預設
            ("", "zh-TW"),
            (None, "zh-TW"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_lang(raw) == expected


class TestTranslationCoverage:
    """翻譯表完整性——確保 _TOOLS_SEED 每個工具都有 en/zh-CN/ru。"""

    def test_all_seed_tools_have_english_translation(self):
        """所有 _TOOLS_SEED 工具都有英文翻譯（Bug#1 核心覆蓋）。"""
        from core.database.tools import _TOOLS_SEED

        seed_ids = {t["tool_id"] for t in _TOOLS_SEED}
        missing = [tid for tid in seed_ids if tid not in _TRANSLATIONS]
        assert not missing, f"缺英文翻譯的工具: {missing}"

    def test_all_translations_have_3_languages(self):
        """每個翻譯 entry 都有 en/zh-CN/ru 三語 name+desc。"""
        incomplete = []
        for tool_id, langs in _TRANSLATIONS.items():
            for lang in ("en", "zh-CN", "ru"):
                entry = langs.get(lang, {})
                if not entry.get("name"):
                    incomplete.append(f"{tool_id}[{lang}].name")
                if not entry.get("desc"):
                    incomplete.append(f"{tool_id}[{lang}].desc")
        assert not incomplete, f"翻譯不齊全: {incomplete}"

    def test_get_translation_missing_returns_none(self):
        """缺譯（工具或語言不存在）回 None，不拋例外。"""
        assert get_translation("nonexistent", "en") is None
        assert get_translation("get_crypto_price", "fr") is None


# ── MCP 工具（crypto-trader + Manifund）──
# MCP 工具動態載入、不在 _TOOLS_SEED，翻譯表必須自帶 4 語（含 zh-TW）。

MCP_TOOL_IDS = [
    # crypto-trader（get_crypto_price 與 seed 同名，走 seed 條目，不列入）
    "get_crypto_market_data",
    "get_crypto_historical_data",
    "search_crypto",
    "get_trending_crypto",
    "get_global_crypto_data",
    # manifund
    "search_projects",
    "get_project",
    "get_comments",
    "recommend_projects",
    "search_users",
    "get_user",
    "get_txns",
    "get_user_balances",
    "list_causes",
]


class TestMCPToolNameI18n:
    """MCP 工具 display_name 4 語覆蓋（線上 15 工具實查後補齊）。"""

    @pytest.mark.parametrize("tool_id", MCP_TOOL_IDS)
    @pytest.mark.parametrize("language", ["zh-TW", "zh-CN", "en", "ru"])
    def test_mcp_tool_resolves_in_all_languages(self, tool_id, language):
        """每個 MCP 工具在 4 語都解析出人類可讀名稱（非 fallback 詞）。"""
        display = get_tool_display_name(tool_id, language)
        fallbacks = {"zh-TW": "工具", "zh-CN": "工具", "en": "tool", "ru": "инструмент"}
        assert display not in fallbacks.values(), (
            f"{tool_id}[{language}] 落到 fallback 詞 {display!r}（MCP 工具缺翻譯）"
        )
        assert "_" not in display, f"{tool_id}[{language}] 洩漏內部 ID: {display}"

    @pytest.mark.parametrize("tool_id", MCP_TOOL_IDS)
    def test_mcp_translation_entry_has_4_languages(self, tool_id):
        """翻譯表中每個 MCP 條目都有 4 語 name+desc（zh-TW 不靠 seed）。"""
        entry = _TRANSLATIONS.get(tool_id)
        assert entry, f"{tool_id} 不在翻譯表"
        for lang in get_supported_languages():
            lang_entry = entry.get(lang, {})
            assert lang_entry.get("name"), f"{tool_id}[{lang}].name 缺"
            assert lang_entry.get("desc"), f"{tool_id}[{lang}].desc 缺"

    def test_mcp_manifund_search_projects_names(self):
        """抽樣驗證具體譯名（防止 fallback 假陽性）。"""
        assert get_tool_display_name("search_projects", "zh-TW") == "Manifund 專案搜尋"
        assert get_tool_display_name("search_projects", "en") == "Manifund Project Search"
        assert get_tool_display_name("list_causes", "ru") == "Список категорий"

    def test_seed_tool_zh_tw_still_prefers_seed(self):
        """回歸：seed 工具 zh-TW 仍用 seed 原文（不被 table 影響）。"""
        assert get_tool_display_name("get_crypto_price", "zh-TW") == "即時加密貨幣價格"


# ── Bug#2：system prompt 輸出語言指示 ──────────────────────────────────────────


class TestOutputLanguagePrompt:
    """Bug#2：_get_system_prompt 4 語都含明確輸出語言指示。"""

    @pytest.fixture
    def agent(self):
        from core.agents.agents.cryptomind_agent import CryptoMindAgent
        from core.agents.prompt_registry import PromptRegistry

        PromptRegistry.load()
        a = CryptoMindAgent.__new__(CryptoMindAgent)
        a.display_name = None
        a.wallet_address = None
        a.user_tier = "free"
        a.llm = None
        return a

    @pytest.mark.parametrize(
        "language,keyword",
        [
            ("en", "English"),
            ("zh-TW", "繁體中文"),
            ("zh-CN", "简体中文"),
            ("ru", "русском"),
        ],
    )
    def test_system_prompt_contains_language_directive(self, agent, language, keyword):
        """各語言 system prompt 都含明確的「用該語言輸出」指示。"""
        prompt = agent._get_system_prompt(language)
        assert keyword in prompt, (
            f"[{language}] system prompt 缺輸出語言指示（期望含 {keyword!r}）。"
            f" 這是 Bug#2 的根因——補上 output_language 後必須持續存在。"
        )

    def test_english_prompt_has_explicit_respond_instruction(self, agent):
        """英文 prompt 必須有明確 'respond in English' 措辭（不只是英文內容）。"""
        prompt = agent._get_system_prompt("en")
        # 必須含明確的「必須用英文回覆」指令，不能只是「剛好是英文內容」
        assert any(
            kw in prompt
            for kw in ["respond in **English**", "MUST respond in", "must be in English"]
        ), "英文 prompt 缺強制語言指令（Bug#2 修復的核心）"

    def test_output_language_section_exists_in_shared_yaml(self):
        """shared.yaml 必須有 output_language key（Bug#2 修復載體）。"""
        from core.agents.prompt_registry import PromptRegistry

        PromptRegistry.load()
        for lang in get_supported_languages():
            val = PromptRegistry.get("shared", "output_language", lang)
            assert val and len(val) > 20, f"output_language[{lang}] 為空或過短"
