"""前端模型清單結構測試（2026-09-02 DANNY 回報）：

1. 選項描述不要有 emoji／圖案（舊 👁 標記改用 vision 欄位＋i18n 文案）。
2. 描述不硬編碼中文——英文是 fallback 標準，多語系在前端 i18n 做。
3. default_model 必須落在清單內（清單過濾時要同步換 default）。
4. default 指向較新世代，過舊模型（上一代旗艦以下）不得出現在前端清單。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.model_config import MODEL_CONFIG, TAG_EN

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")

# 過舊世代：不在前端清單（僅部分仍作為 server fallback 常數保留，如 gpt-4.1-mini）
BANNED_VALUES = {
    "gpt-4.1-mini",
    "gpt-5-mini",
    "gpt-4o",
    # 2026-09-21：OpenAI 文件已列 deprecated
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.5",
    "openai/gpt-5.4",
    "openai/gpt-5.4-mini",
    # DeepSeek 官方退役（deepseek-flash 取代）
    "deepseek-v4-flash",
    "deepseek/deepseek-v4-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "kimi-k2.5",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "glm-4.7-flash",
}


def test_display_strings_have_no_emoji_and_no_cjk():
    for provider, cfg in MODEL_CONFIG.items():
        assert not CJK.search(cfg["display"]), provider
        assert "👁" not in cfg["display"], provider
        for model in cfg["available_models"]:
            assert not CJK.search(model["display"]), (provider, model["value"])
            assert "👁" not in model["display"], (provider, model["value"])


def test_every_entry_has_name_and_known_tag():
    for provider, cfg in MODEL_CONFIG.items():
        for model in cfg["available_models"]:
            assert model.get("name"), (provider, model["value"])
            if "tag" in model:
                assert model["tag"] in TAG_EN, (provider, model["value"], model["tag"])


def test_default_model_is_in_list_or_free_input():
    for provider, cfg in MODEL_CONFIG.items():
        values = {m["value"] for m in cfg["available_models"]}
        if not cfg.get("free_input"):
            assert cfg["default_model"] in values, provider


def test_defaults_point_to_current_generation():
    assert MODEL_CONFIG["openai"]["default_model"].startswith("gpt-5.6")
    assert MODEL_CONFIG["google_gemini"]["default_model"].startswith("gemini-3.8")
    assert MODEL_CONFIG["anthropic"]["default_model"] == "claude-sonnet-5"
    assert MODEL_CONFIG["deepseek"]["default_model"] == "deepseek-flash"


def test_latest_models_are_selectable():
    # 2026-09-21 DANNY：DeepSeek V4.1 Flash / GPT-6 Astra / Claude Fable 5.1 / Gemini 3.8 上線後要選得到
    values = {
        p: {m["value"] for m in cfg["available_models"]}
        for p, cfg in MODEL_CONFIG.items()
    }
    assert "gpt-6-astra" in values["openai"]
    assert "claude-fable-5-1" in values["anthropic"]
    assert "gemini-3.8-flash" in values["google_gemini"]
    assert "deepseek-flash" in values["deepseek"]
    assert "deepseek/deepseek-v4.1-flash" in values["openrouter"]
    assert "openai/gpt-6-astra" in values["openrouter"]
    assert "anthropic/claude-fable-5.1" in values["openrouter"]


def test_outdated_models_pruned_from_frontend_lists():
    for provider, cfg in MODEL_CONFIG.items():
        values = {m["value"] for m in cfg["available_models"]}
        overlap = values & BANNED_VALUES
        assert not overlap, (provider, sorted(overlap))


def test_zh_locales_cover_every_tag():
    # tag 的翻譯只放 zh-TW / zh-CN；en/ru 依設計退回後端英文標準 display
    for locale in ("zh-TW", "zh-CN"):
        data = json.loads(
            (ROOT / "web" / "js" / "i18n" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        tags = data["settings"]["ai"]["modelTag"]
        missing = set(TAG_EN) - set(tags)
        assert not missing, (locale, sorted(missing))
