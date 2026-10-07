"""AI Studio per-agent skill 面板的接線回歸（Mixer Step 4，2026-09-08）。

同 test_ai_studio_preset_edit_wiring.py 模式——前端無單元測試框架，用
原始碼接線斷言取代：
- 每張 agent 卡在工具面板下方渲染 skill 面板（data-skills-panel）
- 勾選 → saveAgentSkills → PUT { skills: [...] }（空清單先擋）
- 重設走 { skills: null }（後端語意：null/[] = 官方預設全上）
- loadConfigs / loadProfiles 有把 skills 與 skills_catalog 收進狀態
- i18n 新鍵（skills*）四語系齊
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
STUDIO_JS = REPO / "web" / "js" / "ai-studio.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")

_SKILL_I18N_KEYS = (
    "skillsLabel",
    "skillsDefaultHint",
    "skillsSave",
    "skillsReset",
    "skillsSaved",
    "skillsResetDone",
    "skillsSaveFailed",
    "skillsNeedOne",
    "skillsOfficialGroup",
    "skillsCustomGroup",
)


def _js() -> str:
    return STUDIO_JS.read_text(encoding="utf-8")


def _locale(lang: str) -> dict:
    return json.loads((REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8"))


class TestSkillsPanelWiring:
    def test_card_assembly_includes_skills_panel(self):
        js = _js()
        # 卡片組裝：工具面板之後接 skill 面板；binding 也接上
        assert "this._agentToolsHtml(p) +" in js
        idx_tools = js.index("this._agentToolsHtml(p) +")
        idx_skills = js.index("this._agentSkillsHtml(p) +", idx_tools)
        assert idx_skills > idx_tools
        assert "_bindAgentSkillPanels(el);" in js

    def test_skills_panel_uses_distinct_dom_markers(self):
        js = _js()
        # 與工具面板的 DOM marker 分開——查詢/取代才不會互相踩
        assert 'data-skills-panel="' in js
        assert 'data-agent-skill="' in js
        assert "[data-skills-count]" in js
        # 工具面板的選擇器不會誤抓 skill 面板（兩組 marker 互異）
        assert 'input[data-agent-skill]' in js

    def test_save_sends_skills_payload_and_blocks_empty(self):
        js = _js()
        # saveAgentSkills → _putAgentSkills(agentId, { skills: names })
        assert "this._putAgentSkills(agentId, { skills: names });" in js
        # 空清單先擋——skills=[] 在後端是「未設定＝全部」，直接送會反轉語意
        save_fn = js[js.index("saveAgentSkills(agentId) {") : js.index("resetAgentSkills(agentId) {")]
        assert "skillsNeedOne" in save_fn
        assert "if (!names.length)" in save_fn

    def test_reset_sends_null(self):
        js = _js()
        assert "this._putAgentSkills(agentId, { skills: null });" in js

    def test_state_stores_skills_and_catalog(self):
        js = _js()
        # GET agent-configs 收 skills；GET agent-profiles 收 skills_catalog
        assert "skills: Array.isArray(c.skills) ? c.skills : []," in js
        assert "this._skillsCatalog = Array.isArray(data.skills_catalog)" in js
        # reconcile 只碰 skills 欄（不整頁重抓——保其他卡的草稿）
        put_fn = js[js.index("async _putAgentSkills(agentId, skillsPayload) {") : js.index("saveAgentSkills(agentId) {")]
        assert "skills: data.config.skills," in put_fn

    def test_unset_selection_renders_all_checked(self):
        js = _js()
        # 未設定（stored 空）＝全部勾（官方預設）——與工具的初始狀態同語意
        initial_fn = js[js.index("_initialSkillSelection(profile) {") : js.index("_agentSkillsHtml(profile) {")]
        assert "if (!stored.length) return new Set(catalog.map((s) => s.name));" in initial_fn


class TestSkillsI18n:
    @pytest.mark.parametrize("lang", list(LOCALES))
    def test_skill_keys_present_all_locales(self, lang):
        locale = _locale(lang)
        ai = locale.get("aiStudio", {})
        missing = [k for k in _SKILL_I18N_KEYS if not str(ai.get(k, "")).strip()]
        assert not missing, f"aiStudio 缺少 skill i18n 鍵（{lang}）：{missing}"
