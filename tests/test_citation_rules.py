"""守護測試:確保 citation_rules 各語言都禁止 LLM 使用 HTML 標籤。

背景(2026-07):LLM 偶爾自作主張用 <sup>[N]</sup> 做行內引用編號,但前端
渲染層(markdown-it html:false / escapeHtml)會把這些標籤當字面文字顯示,
造成 UI 出現難看的 <sup>[3]</sup>。修復分兩層:

1. Prompt 層(shared.yaml citation_rules):明文禁止 HTML 標籤(治本)
2. 渲染層(app.js stripInlineHtml):渲染前先 strip(防禦深度)

本測試守護第 1 層 — 確保四個語言的 citation_rules 都包含「禁止 HTML」規則,
未來若有人編輯 prompt 不小心拿掉,測試會失敗。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SHARED_YAML = Path(__file__).resolve().parents[1] / "core" / "agents" / "prompts" / "shared.yaml"

# 各語言在「禁止 HTML」規則中應提及的關鍵標籤(確認規則具體而非空泛)
EXPECTED_HTML_MENTION = "<sup>"

# 各語言規則文字中應出現的「禁止 HTML」語意關鍵詞
LANG_MARKERS = {
    "zh-TW": "禁止使用 HTML 標籤",
    "zh-CN": "禁止使用 HTML 标签",
    "en": "Do NOT use HTML tags",
    "ru": "Не используйте HTML-теги",
}


@pytest.mark.unit
def test_citation_rules_forbid_html_in_all_languages():
    """citation_rules 的四個語言都必須明文禁止 HTML 標籤。"""
    data = yaml.safe_load(SHARED_YAML.read_text(encoding="utf-8"))
    citation_rules = data.get("citation_rules")
    assert citation_rules, "shared.yaml 缺少 citation_rules 區塊"

    missing_lang = []
    missing_marker = []
    missing_sup_mention = []

    for lang, marker in LANG_MARKERS.items():
        rules_text = citation_rules.get(lang)
        if rules_text is None:
            missing_lang.append(lang)
            continue
        if marker not in rules_text:
            missing_marker.append(f"{lang}: 缺少「{marker}」")
        if EXPECTED_HTML_MENTION not in rules_text:
            missing_sup_mention.append(f"{lang}: 未具體提及 <sup> 標籤")

    assert not missing_lang, f"citation_rules 缺少語言版本: {missing_lang}"
    assert not missing_marker, (
        "以下語言的 citation_rules 缺少「禁止 HTML」規則:\n  "
        + "\n  ".join(missing_marker)
    )
    assert not missing_sup_mention, (
        "以下語言的「禁止 HTML」規則未具體提及 <sup>(規則應具體而非空泛):\n  "
        + "\n  ".join(missing_sup_mention)
    )
