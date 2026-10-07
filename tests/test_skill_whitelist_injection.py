"""Mixer Step 4：per-agent skill 白名單的 runtime 注入過濾測試。

設計：docs/plans/2026-09-08-mixer-step4-per-agent-skills-design.md。

釘住 `base_react_agent._inject_skill_instructions` 對
`preset_config.agent_skill_selections` 的四個施加點：
1. catalog 注入（match_all_skills，CLAW 路徑）：白名單外的官方 skill 不進目錄
2. eager_load：白名單外不注入全文
3. keyword-match（非 CLAW agent）：白名單外不注入 instructions
4. custom skill block：白名單外的自訂 skill 不注入

以及兩個語意保證：
- 未設定（無 selections）→ 行為與現行完全一致（catalog 全上）
- 多 agent preset → 白名單取聯集；Settings disabled 與白名單兩層都生效
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.agents.base_react_agent import BaseReActAgent

pytestmark = pytest.mark.unit

# repo 真實存在的官方 skill 名（core/agents/skills/）
_SK_CRYPTO = "crypto-technical-analysis"
_SK_US = "us-stock-technical"
_SK_JUDGE = "investment-judgment"


def _make_agent(name="cryptomind", match_all_skills=True, user_id="u1"):
    # name 是 abstract property——用子類帶入（__new__ 跳過重量級 __init__）
    agent_cls = type(f"_T_{name}", (BaseReActAgent,), {
        "name": name,
        "match_all_skills": match_all_skills,
    })
    agent = agent_cls.__new__(agent_cls)
    agent.user_id = user_id
    return agent


def _task(query, preset_config=None):
    ctx = {"original_query": query}
    if preset_config is not None:
        ctx["preset_config"] = preset_config
    return SimpleNamespace(context=ctx, description=query)


def _no_user_prefs(self, query="", allowed_skills=None):
    """隔離 DB：使用者偏好全空（disabled 無、custom 無）。"""
    return set(), ""


class TestPresetSkillWhitelist:
    def test_no_preset_returns_none(self):
        agent = _make_agent()
        assert agent._preset_skill_whitelist(_task("BTC")) is None

    def test_selections_without_matching_agent_returns_none(self):
        agent = _make_agent()
        task = _task(
            "BTC",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"general_research": [_SK_CRYPTO]},
            },
        )
        assert agent._preset_skill_whitelist(task) is None

    def test_union_across_preset_agents(self):
        agent = _make_agent()
        task = _task(
            "BTC",
            preset_config={
                "agent_ids": ["finance_markets", "general_research"],
                "agent_skill_selections": {
                    "finance_markets": [_SK_CRYPTO],
                    "general_research": [_SK_US],
                },
            },
        )
        assert agent._preset_skill_whitelist(task) == {_SK_CRYPTO, _SK_US}

    def test_empty_selections_return_none(self):
        """空清單＝未設定（官方預設全上）——不是空集合過濾掉一切。"""
        agent = _make_agent()
        task = _task(
            "BTC",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": []},
            },
        )
        assert agent._preset_skill_whitelist(task) is None


class TestCatalogFiltering:
    def test_whitelist_limits_catalog(self):
        agent = _make_agent()
        task = _task(
            "BTC 技術分析",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": [_SK_CRYPTO]},
            },
        )
        with patch.object(BaseReActAgent, "_load_user_skill_prefs", _no_user_prefs):
            prompt = agent._inject_skill_instructions("BASE", task)
        assert _SK_CRYPTO in prompt
        assert _SK_US not in prompt
        assert _SK_JUDGE not in prompt

    def test_unset_selections_keep_full_catalog(self):
        """未設定 → 現行行為：整個官方 skill 目錄都在（回歸保護）。"""
        agent = _make_agent()
        task = _task(
            "BTC 技術分析",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {},
            },
        )
        with patch.object(BaseReActAgent, "_load_user_skill_prefs", _no_user_prefs):
            prompt = agent._inject_skill_instructions("BASE", task)
        assert _SK_CRYPTO in prompt
        assert _SK_US in prompt

    def test_settings_disabled_intersects_whitelist(self):
        """Settings 關閉（per-user）與 preset 白名單是兩層 AND——都在線上時取交集。"""
        agent = _make_agent()
        task = _task(
            "BTC 技術分析",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": [_SK_CRYPTO, _SK_US]},
            },
        )
        with patch.object(
            BaseReActAgent,
            "_load_user_skill_prefs",
            lambda self, query="", **_kw: ({_SK_CRYPTO}, ""),
        ):
            prompt = agent._inject_skill_instructions("BASE", task)
        assert _SK_CRYPTO not in prompt  # Settings 關了
        assert _SK_US in prompt  # 白名單內且未被 Settings 關


class TestKeywordMatchFiltering:
    """非 CLAW agent（match_all_skills=False）：harness 端 keyword-match 注入。"""

    def test_whitelisted_skill_injects_instructions(self):
        agent = _make_agent(name="crypto", match_all_skills=False)
        task = _task(
            "BTC 技術分析",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": [_SK_CRYPTO]},
            },
        )
        with patch.object(BaseReActAgent, "_load_user_skill_prefs", _no_user_prefs):
            prompt = agent._inject_skill_instructions("BASE", task)
        assert "# Crypto Technical Analysis" in prompt  # 全文注入

    def test_non_whitelisted_skill_not_injected(self):
        agent = _make_agent(name="crypto", match_all_skills=False)
        task = _task(
            "BTC 技術分析",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": [_SK_JUDGE]},  # 不含 crypto skill
            },
        )
        with patch.object(BaseReActAgent, "_load_user_skill_prefs", _no_user_prefs):
            prompt = agent._inject_skill_instructions("BASE", task)
        assert "# Crypto Technical Analysis" not in prompt


class TestCustomSkillFiltering:
    _CUSTOM = [{
        "skill_name": "my-tx-playbook",
        "description": "TX 鏈交易 SOP",
        "trigger_keywords": "tx",
        "body": "只在鏈上數據乾淨時進場。",
    }]

    def _patch_store(self):
        return (
            patch(
                "core.database.skill_preferences.SkillPreferenceStore.get_disabled_skills",
                return_value=set(),
            ),
            patch(
                "core.database.skill_preferences.SkillPreferenceStore.get_enabled_custom_skills",
                return_value=list(self._CUSTOM),
            ),
        )

    def test_custom_skill_in_whitelist_injected(self):
        agent = _make_agent()
        task = _task(
            "TX 鏈現在人聲鼎沸適合進場嗎",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": ["my-tx-playbook"]},
            },
        )
        p_disabled, p_custom = self._patch_store()
        with p_disabled, p_custom:
            prompt = agent._inject_skill_instructions("BASE", task)
        assert "my-tx-playbook" in prompt

    def test_custom_skill_outside_whitelist_not_injected(self):
        agent = _make_agent()
        task = _task(
            "TX 鏈現在人聲鼎沸適合進場嗎",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {"finance_markets": [_SK_CRYPTO]},
            },
        )
        p_disabled, p_custom = self._patch_store()
        with p_disabled, p_custom:
            prompt = agent._inject_skill_instructions("BASE", task)
        assert "my-tx-playbook" not in prompt

    def test_custom_skill_injected_when_unset(self):
        """未設定白名單 → 自訂 skill 照舊全注入（回歸保護）。"""
        agent = _make_agent()
        task = _task(
            "TX 鏈現在人聲鼎沸適合進場嗎",
            preset_config={
                "agent_ids": ["finance_markets"],
                "agent_skill_selections": {},
            },
        )
        p_disabled, p_custom = self._patch_store()
        with p_disabled, p_custom:
            prompt = agent._inject_skill_instructions("BASE", task)
        assert "my-tx-playbook" in prompt
