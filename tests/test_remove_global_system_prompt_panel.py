"""移除 chat「Analysis Settings → Custom System Prompt（global）」面板
（2026-09-10 DANNY：system prompt 已進駐 AI Studio 的 per-agent 設定，
全域面板冗餘，移除）。

語義盘点（移除依據）：
- AI Studio per-agent prompt（#717，user_agent_configs.system_prompt）：
  4 個分析 agent 各自的專屬指示
- chat 面板全域 prompt（user_analysis_preferences.system_prompt,
  agent_id='chat'）：state.system_prompt 全域層——與 per-agent 並存但
  語義重疊（都要指示 AI 行為），DANNY 裁決移除全域入口
- 既有資料留在 user_analysis_preferences（enabled_tools 的 legacy
  fallback 仍讀）——只移 UI／送線／system_prompt 解析，資料不動
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestPanelRemoved:
    def test_no_panel_markup(self):
        src = _read("web/index.html")
        assert "system-prompt-input" not in src
        assert "analysis-options-panel" not in src
        assert "system-prompt-premium-lock" not in src

    def test_options_button_removed(self):
        """面板唯一內容就是 prompt 區塊——按鈕跟著退場，不留死鍵。"""
        src = _read("web/index.html")
        assert 'data-click="toggleOptions"' not in src

    def test_module_and_handlers_removed(self):
        assert not (REPO / "web/js/analysisSettings.js").exists()
        assert "analysisSettings" not in _read("web/js/main.js")
        src = _read("web/js/click-delegator.js")
        assert "'systemPrompt'" not in src and "'systemPromptBlur'" not in src
        assert "'toggleOptions'" not in src

    def test_chat_send_no_system_prompt(self):
        src = _read("web/js/chat-analysis.js")
        assert "system_prompt" not in src, (
            "前端聊天請求不得再送 system_prompt（per-agent 已由 AI Studio 承接）"
        )


class TestBackendResolutionRemoved:
    def test_stored_chat_prompt_no_longer_resolved(self):
        """停用已存全域 prompt 的解析（既有資料不刪，只是不再注入）。"""
        src = _read("api/routers/analysis.py")
        assert not re.search(r"resolved_system_prompt = chat_pref\.system_prompt", src), (
            "不得再從 user_analysis_preferences 解析 system_prompt"
        )

    def test_enabled_tools_fallback_kept(self):
        """同一筆偏好的 enabled_tools legacy fallback 保留。"""
        src = _read("api/routers/analysis.py")
        assert re.search(r"resolved_enabled_tools = \(?\s*list\(chat_pref\.enabled_tools\)", src) or (
            "chat_pref.enabled_tools" in src
        )

    def test_sanitize_guard_kept(self):
        """body.system_prompt（其他客戶端可能直送）的 sanitize 防護保留。"""
        src = _read("api/routers/analysis.py")
        assert "sanitize_system_prompt" in src


class TestI18nCleanup:
    def test_dead_keys_removed(self):
        import json

        for lang in ("en", "zh-TW", "zh-CN", "ru"):
            data = json.loads(_read(f"web/js/i18n/{lang}.json"))
            for ns, keys in (
                ("settings", ("systemPrompt", "saveFailed", "upgradePremiumHint", "toolEnableHint", "analysisSettings")),
                ("chat", ("systemPromptPlaceholder", "options")),
            ):
                for key in keys:
                    assert key not in data.get(ns, {}), (
                        f"{lang}.json {ns}.{key} 殘留（forum.upgradePremiumHint 等"
                        "同名異層 key 不在此限）"
                    )

    def test_saved_key_kept_for_ai_studio(self):
        src = _read("web/js/i18n/en.json")
        assert '"saved"' in src, "settings.saved 由 ai-studio.js 使用，必須保留"
