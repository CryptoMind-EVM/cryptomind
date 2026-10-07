"""Preset 編輯 UI 的接線回歸（2026-09-08）。

背景：``PATCH /api/agent-presets/{preset_id}`` 後端自 Mixer Step 起就存在
（agent_presets.py:214，全欄位可選、Premium-only、server 端 catalog 驗證），
但前端只接了 GET / POST / activate / delete——「不能重新編輯」是缺 UI，
不是後端限制。本 PR 補上：列表卡「編輯」鈕 → 開啟既有表單預填 → PATCH。

本檔守住（wiring 測試，同 test_sw_mixguard_wiring.py 模式——前端無單元
測試框架，用原始碼接線斷言取代）：
- 每張 Preset 卡有「編輯」鈕，走與 activate/delete 相同的 data-click 委派
- submitPresetForm 依 _editingPresetId 分流 POST / PATCH
- 編輯不得把既有 analysis_mode 悄悄降級成 'quick'（UI 沒有這個欄位，
  建立時固定 quick；編輯必須沿用該 Preset 現值）
- 編輯不送 is_default：啟用是「啟用」鈕的職責，編輯表單順便切換
  作用中 Preset 會讓使用者以為只是改了名字卻換掉了作用範圍
- 取消／關閉／重新開建立表單都要清 _editingPresetId——殘留會讓下一次
  「建立」變成「編輯」某個舊 Preset（資料被覆寫）
- i18n 新鍵四語系齊
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
STUDIO_JS = REPO / "web" / "js" / "ai-studio.js"
TEMPLATE = REPO / "web" / "js" / "components" / "tab-ai-studio.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")


def _js() -> str:
    return STUDIO_JS.read_text(encoding="utf-8")


def _fn_body(js: str, signature: str) -> str:
    """切出函數本體（到第一個行首 ``}`` 為止）——同 mixguard 測試的切法。"""
    start = js.index(signature)
    return js[start : js.index("\n    },", start)]


def _locale(lang: str) -> dict:
    return json.loads((REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8"))


class TestEditButtonWiring:
    def test_every_preset_card_has_edit_button(self):
        body = _fn_body(_js(), "renderPresets() {")
        assert 'data-click="AIStudioTab.editPreset"' in body, "列表卡缺「編輯」鈕"
        # 與 activate/delete 同一條委派路徑（click-delegator.js）
        assert "data-click-arg=" in body

    def test_editPreset_exists_and_prefills_the_form(self):
        js = _js()
        body = _fn_body(js, "async editPreset(presetId) {")
        assert "this.openCreatePreset()" in body, "編輯要重用建立表單的 checkbox 骨架"
        assert "ai-studio-preset-name" in body, "沒有預填名稱"
        assert "ai-studio-agent-cb" in body, "沒有預勾 agent"
        assert "ai-studio-preset-mode" in body, "沒有預選模式"
        assert "ai-studio-preset-policy" in body, "沒有預選工具權限"
        # 被 capability_overrides 關掉的能力，編輯時要呈現為未勾選
        assert "ai-studio-cap-cb" in body


class TestSubmitBranching:
    def test_patches_when_editing_posts_otherwise(self):
        body = _fn_body(_js(), "async submitPresetForm() {")
        assert "AppAPI.patch(" in body, "編輯路徑必須走 PATCH"
        assert "'/api/agent-presets/' + encodeURIComponent(this._editingPresetId)" in body
        assert "AppAPI.post('/api/agent-presets'" in body, "建立路徑仍是 POST"

    def test_edit_preserves_existing_analysis_mode(self):
        """UI 沒有 analysis_mode 欄位；編輯硬送 'quick' 會把 verified/research 悄悄降級。"""
        body = _fn_body(_js(), "async submitPresetForm() {")
        assert "analysis_mode: editing ?" in body or "analysis_mode = editing ?" in body, (
            "analysis_mode 必須依「是否編輯」分流，不得兩條路徑同值硬編"
        )
        assert "editing.analysis_mode" in body, "編輯路徑要沿用該 Preset 的現值"

    def test_edit_does_not_send_is_default(self):
        """is_default:true 會順便切作用中 Preset；false 是噪音。編輯表單兩者都不送。"""
        body = _fn_body(_js(), "async submitPresetForm() {")
        # 只切 PATCH 呼叫自身的參數區（到第一個 ``);``）——切到 AppAPI.post
        # 會把 else 分支裡的 payload.is_default = false 誤算進來
        patch_args = body.split("AppAPI.patch(", 1)[1].split(");", 1)[0]
        assert "is_default" not in patch_args, "PATCH 分支不得送 is_default"


class TestEditingStateLifecycle:
    def test_state_field_exists(self):
        assert "_editingPresetId: null" in _js(), "AIStudioTab 缺 _editingPresetId 初始態"

    @pytest.mark.parametrize(
        "signature",
        [
            "closePresetForm() {",
            "openCreatePreset() {",
        ],
    )
    def test_every_exit_and_create_entry_clears_editing_state(self, signature):
        body = _fn_body(_js(), signature)
        assert "_editingPresetId = null" in body, f"{signature} 沒有清編輯態"

    def test_title_swaps_between_create_and_edit(self):
        js = _js()
        edit_body = _fn_body(js, "async editPreset(presetId) {")
        create_body = _fn_body(js, "openCreatePreset() {")
        # data-i18n 屬性同步換：語言切換重套 i18n 時標題才不會跳回「新增」
        assert "aiStudio.editPresetTitle" in edit_body
        assert "aiStudio.newPresetTitle" in create_body

    def test_form_title_has_id_in_template(self):
        tpl = TEMPLATE.read_text(encoding="utf-8")
        assert 'id="ai-studio-preset-form-title"' in tpl, "標題元素缺 id，JS 換不了字"

    def test_template_buttons_target_renamed_handlers(self):
        tpl = TEMPLATE.read_text(encoding="utf-8")
        assert 'data-click="AIStudioTab.submitPresetForm"' in tpl
        assert 'data-click="AIStudioTab.closePresetForm"' in tpl

    def test_old_handler_names_are_gone(self):
        """submitPresetForm 同時管建立與編輯——舊名 submitCreatePreset 會讓人
        以為它只 POST，複製貼上時做出第二條純 POST 路徑。"""
        for path in (STUDIO_JS, TEMPLATE):
            src = path.read_text(encoding="utf-8")
            assert "submitCreatePreset" not in src, f"{path.name} 仍含舊名 submitCreatePreset"
            assert "closeCreatePreset" not in src, f"{path.name} 仍含舊名 closeCreatePreset"


class TestI18nAllLocales:
    @pytest.mark.parametrize("lang", LOCALES)
    @pytest.mark.parametrize("key", ["edit", "editPresetTitle", "updateFailed"])
    def test_new_keys_exist(self, lang, key):
        value = _locale(lang)["aiStudio"].get(key)
        assert value, f"{lang}: aiStudio.{key} 缺漏"

    @pytest.mark.parametrize("lang", LOCALES)
    def test_update_failed_gets_used(self, lang):
        """錯誤標題鍵要真的被引用，不是只躺在 JSON 裡（防孤兒 key）。"""
        assert "aiStudio.updateFailed" in _js()
        assert _locale(lang)["aiStudio"].get("updateFailed")
