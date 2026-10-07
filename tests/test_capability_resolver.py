"""Capability Resolver 測試 — core/agents/capability_resolver.py（impl plan Task A2）。

驗證設計 §6.3 的權限公式：每一層只能縮小、絕不擴權；fail-closed 回 general_research。
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from core.agents.capability_resolver import (
    PresetView,
    compute_config_hash,
    resolve_preset_config,
    resolve_tool_pool,
)
from core.agents.profile_catalog import load_catalog
from core.agents.tool_registry import ToolMetadata

CATALOG = load_catalog()

# 測試工具宇宙：涵蓋 native（有 category）與 MCP（無 category、靠 capability 名稱）
FAKE_TOOLS = [
    ToolMetadata(name="web_search", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="get_crypto_price", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="technical_analysis", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="check_wallet_address", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="search_projects", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="get_project", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="get_comments", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="get_user_balances", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="get_txns", description="", input_schema={}, handler=lambda **k: None),
    ToolMetadata(name="premium_deep_analysis", description="", input_schema={}, handler=lambda **k: None, required_tier="premium"),
    ToolMetadata(name="dangerous_action_tool", description="", input_schema={}, handler=lambda **k: None, risk_level="high"),
]

CATEGORY_OF = {
    "web_search": "general",
    "get_crypto_price": "crypto_basic",
    "technical_analysis": "technical",
    "check_wallet_address": "onchain",
    "premium_deep_analysis": "finance",
    "dangerous_action_tool": "finance",
}


def _resolve(preset: PresetView, **kwargs):
    defaults = dict(
        tool_metas=FAKE_TOOLS,
        preset=preset,
        user_tier="free",
        category_of=lambda name: CATEGORY_OF.get(name),
        catalog=CATALOG,
    )
    defaults.update(kwargs)
    return resolve_tool_pool(**defaults)


class TestProfileFilter:
    def test_people_projects_excludes_market_tools(self):
        preset = PresetView(agent_ids=("people_projects",))
        result = _resolve(preset)
        assert "get_crypto_price" not in result.tool_names
        assert "technical_analysis" not in result.tool_names

    def test_people_projects_includes_discover_and_detail_tools(self):
        preset = PresetView(agent_ids=("people_projects",))
        result = _resolve(preset)
        assert {"search_projects", "get_project", "get_comments"} <= result.tool_names

    def test_advanced_funds_default_disabled(self):
        """advanced_funds（餘額／交易）預設關閉——design §15.3。"""
        preset = PresetView(agent_ids=("people_projects",))
        result = _resolve(preset)
        assert "get_user_balances" not in result.tool_names
        assert "get_txns" not in result.tool_names

    def test_onchain_security_includes_onchain_category(self):
        preset = PresetView(agent_ids=("onchain_security",))
        result = _resolve(preset)
        assert "check_wallet_address" in result.tool_names
        assert "web_search" not in result.tool_names  # general 不在 onchain profile

    def test_team_mode_unions_profiles(self):
        preset = PresetView(agent_ids=("people_projects", "onchain_security"), mode="team")
        result = _resolve(preset)
        assert "check_wallet_address" in result.tool_names
        assert "get_project" in result.tool_names


class TestCapabilityOverrides:
    def test_disable_discover_removes_search_tools(self):
        preset = PresetView(
            agent_ids=("people_projects",),
            capability_overrides={"discover": False},
        )
        result = _resolve(preset)
        assert "search_projects" not in result.tool_names
        assert "get_project" in result.tool_names  # 其他 capability 不受影響

    def test_enabling_capability_not_in_profile_ignored_with_warning(self):
        """開啟 profile 未含的 capability 不得擴權。"""
        preset = PresetView(
            agent_ids=("general_research",),
            capability_overrides={"discover": True},
        )
        result = _resolve(preset)
        assert "search_projects" not in result.tool_names
        assert any("not in selected profiles" in w for w in result.warnings)

    def test_unknown_capability_ignored_with_warning(self):
        preset = PresetView(
            agent_ids=("people_projects",),
            capability_overrides={"quantum_thing": False},
        )
        result = _resolve(preset)
        assert any("unknown capability" in w for w in result.warnings)

    def test_invalid_override_type_ignored(self):
        preset = PresetView(
            agent_ids=("people_projects",),
            capability_overrides={"discover": "yes"},
        )
        result = _resolve(preset)
        assert any("invalid capability override" in w for w in result.warnings)


class TestTierFilter:
    def test_free_user_cannot_get_premium_tool(self):
        preset = PresetView(agent_ids=("finance_markets",))
        result = _resolve(preset, user_tier="free")
        assert "premium_deep_analysis" not in result.tool_names

    def test_premium_user_gets_premium_tool(self):
        preset = PresetView(agent_ids=("finance_markets",))
        result = _resolve(preset, user_tier="premium")
        assert "premium_deep_analysis" in result.tool_names


class TestActionPolicy:
    def test_read_only_excludes_high_risk(self):
        preset = PresetView(agent_ids=("finance_markets",), action_policy="read_only")
        result = _resolve(preset, user_tier="premium")
        assert "dangerous_action_tool" not in result.tool_names

    def test_confirm_actions_keeps_high_risk(self):
        preset = PresetView(agent_ids=("finance_markets",), action_policy="confirm_actions")
        result = _resolve(preset, user_tier="premium")
        assert "dangerous_action_tool" in result.tool_names


class TestNarrowOnly:
    def test_authorized_tool_ids_narrows(self):
        preset = PresetView(agent_ids=("general_research",))
        result = _resolve(preset, authorized_tool_ids=["web_search"])
        assert result.tool_names == frozenset({"web_search"})

    def test_client_enabled_tools_cannot_expand(self):
        """client 要求不在 profile 內的工具 → 交集後拿不到（不可擴權）。"""
        preset = PresetView(agent_ids=("general_research",))
        result = _resolve(preset, client_enabled_tools=["get_crypto_price", "web_search"])
        assert result.tool_names == frozenset({"web_search"})

    def test_client_enabled_tools_empty_list_means_no_request(self):
        """空 list = 使用者沒指定，不該鎖死全部。"""
        preset = PresetView(agent_ids=("general_research",))
        result = _resolve(preset, client_enabled_tools=[])
        assert "web_search" in result.tool_names


class TestFailClosed:
    def test_unknown_profiles_dropped_with_warning(self):
        preset = PresetView(agent_ids=("people_projects", "ghost_profile"))
        result = _resolve(preset)
        assert any("ghost_profile" in w for w in result.warnings)
        assert "people_projects" in result.profiles_used

    def test_all_invalid_falls_back_to_general_research(self):
        preset = PresetView(agent_ids=("nope",))
        result = _resolve(preset)
        assert result.profiles_used == ("general_research",)
        assert "web_search" in result.tool_names
        assert "get_crypto_price" not in result.tool_names

    def test_empty_pool_falls_back_to_general_research(self):
        """授權集合跟 onchain_security 完全無交集 → fail-closed 退 General。"""
        preset = PresetView(agent_ids=("onchain_security",))
        result = _resolve(preset, authorized_tool_ids=["web_search"])
        assert result.profiles_used == ("general_research",)
        assert result.tool_names == frozenset({"web_search"})
        assert any("fail-closed retry" in w for w in result.warnings)

    def test_empty_pool_after_fallback_stays_empty(self):
        """連 General 都拿不到任何工具 → 空集合（絕不回全部工具）。"""
        preset = PresetView(agent_ids=("people_projects",))
        result = _resolve(preset, authorized_tool_ids=["not_a_real_tool"])
        assert result.tool_names == frozenset()


class TestConfigHash:
    def test_hash_deterministic(self):
        preset = PresetView(agent_ids=("general_research",))
        a = _resolve(preset)
        b = _resolve(preset)
        assert a.config_hash == b.config_hash

    def test_hash_changes_with_pool(self):
        free = _resolve(PresetView(agent_ids=("general_research",)), user_tier="free")
        premium = _resolve(PresetView(agent_ids=("general_research",)), user_tier="premium")
        assert free.config_hash != premium.config_hash

    def test_compute_config_hash_stable(self):
        assert compute_config_hash({"a": 1, "b": 2}) == compute_config_hash({"b": 2, "a": 1})


class TestResolvePresetConfig:
    def test_end_to_end_with_seed_categories(self):
        """用真實 _TOOLS_SEED categories 跑 API 便利入口。"""
        config = resolve_preset_config(
            {
                "agent_ids": ["people_projects"],
                "action_policy": "read_only",
                "analysis_mode": "quick",
            },
            user_tier="free",
        )
        assert config["action_policy"] == "read_only"
        assert config["profiles"] == ["people_projects"]
        assert config["tool_names"], "people_projects should keep at least general tools"
        # 不得出現 crypto 交易類工具
        assert not any("trade" in t or "swap" in t for t in config["tool_names"])
        # Manifund 工具（discover/detail/comments）應在；advanced_funds 不應在
        assert "search_projects" in config["tool_names"]
        assert "get_user_balances" not in config["tool_names"]
        assert config["config_hash"]

    def test_preset_config_is_json_serializable(self):
        """preset_config 會進 graph_input → 必須可序列化。"""
        import json

        config = resolve_preset_config({"agent_ids": ["general_research"]}, user_tier="free")
        json.dumps(config)


class TestAgentToolSelection:
    """Mixer Step 2：第 8 層使用者 per-agent 勾選（白名單）＋必要工具保護。

    對應 design：docs/plans/2026-09-03-model-mixer-step2-tools.md §2／§3。
    測試宇宙（FAKE_TOOLS）裡 general_research 的預設池 = {web_search}
    （premium_deep_analysis 被 tier 濾掉、dangerous_action_tool 被 read_only 濾掉）。
    """

    def test_selection_narrows_pool(self):
        preset = PresetView(agent_ids=("onchain_security",))
        result = _resolve(
            preset, agent_tool_selections={"onchain_security": ["get_crypto_price"]}
        )
        assert result.tool_names == frozenset({"get_crypto_price"})

    def test_no_selection_means_no_filtering(self):
        preset = PresetView(agent_ids=("onchain_security",))
        baseline = _resolve(preset)
        for selections in (None, {}, {"general_research": ["web_search"]}):
            result = _resolve(preset, agent_tool_selections=selections)
            assert result.tool_names == baseline.tool_names

    def test_empty_selection_list_means_unconfigured(self):
        """空清單 = 未設定（官方預設），不得鎖死全部工具。"""
        preset = PresetView(agent_ids=("onchain_security",))
        baseline = _resolve(preset)
        result = _resolve(preset, agent_tool_selections={"onchain_security": []})
        assert result.tool_names == baseline.tool_names

    def test_selection_cannot_expand_beyond_pool(self):
        """勾了 profile 不允許／上游層濾掉的工具 → 交集自然無效，不擴權。"""
        preset = PresetView(agent_ids=("general_research",))
        result = _resolve(
            preset,
            agent_tool_selections={
                "general_research": [
                    "get_crypto_price",  # crypto_basic 不在 general profile
                    "premium_deep_analysis",  # tier 濾掉
                    "dangerous_action_tool",  # read_only 濾掉
                    "totally_unknown_tool",  # 不存在
                ]
            },
        )
        assert result.tool_names == frozenset({"web_search"})

    def test_required_tool_readded_after_selection(self):
        """使用者取消勾選必要工具 → 回補並記 warning。"""
        preset = PresetView(agent_ids=("general_research",))
        result = _resolve(
            preset, agent_tool_selections={"general_research": ["totally_unknown_tool"]}
        )
        assert "web_search" in result.tool_names
        assert any("required tools re-added" in w for w in result.warnings)

    def test_required_readd_never_exceeds_pre_selection_pool(self):
        """必要工具保護不越過第 8 層前的池子——被授權層濾掉的不得復活。"""
        preset = PresetView(agent_ids=("onchain_security",))
        result = _resolve(
            preset,
            authorized_tool_ids={"get_crypto_price", "technical_analysis"},
            agent_tool_selections={"onchain_security": ["get_crypto_price"]},
        )
        assert result.tool_names == frozenset({"get_crypto_price"})

    def test_multi_agent_unconfigured_agent_stays_unrestricted(self):
        """多 agent preset：未設定的 agent = 官方預設不設限，其工具不得被他人勾選縮掉。"""
        preset = PresetView(agent_ids=("general_research", "onchain_security"))
        result = _resolve(
            preset, agent_tool_selections={"general_research": ["web_search"]}
        )
        # onchain_security 未設定 → 它自己的池子完整保留
        assert {"web_search", "get_crypto_price", "technical_analysis"} <= result.tool_names

    def test_multi_agent_both_configured_intersect_union(self):
        preset = PresetView(agent_ids=("general_research", "onchain_security"))
        result = _resolve(
            preset,
            agent_tool_selections={
                "general_research": ["web_search"],
                "onchain_security": ["check_wallet_address"],
            },
        )
        assert result.tool_names == frozenset({"web_search", "check_wallet_address"})

    def test_configured_agent_only_contributes_its_own_tools(self):
        preset = PresetView(agent_ids=("general_research", "onchain_security"))
        result = _resolve(
            preset,
            agent_tool_selections={
                "general_research": ["web_search", "get_crypto_price"],
                "onchain_security": ["check_wallet_address"],
            },
        )
        # A globally valid tool is still outside general_research's candidate pool.
        # It must not undo the owning agent's explicit deselection in a shared pool.
        assert result.tool_names == frozenset({"web_search", "check_wallet_address"})

    def test_multi_agent_shadowed_deselection_warns(self):
        """共用池下取消勾選被別的 agent 擋回來 → 必須留下 warning（靜默 no-op 揭露）。

        finance_markets 與 onchain_security 都吃 crypto_basic：前者取消勾選
        get_crypto_price，後者未設定仍貢獻它 → 池子縮不掉，使用者卻只看到
        「已儲存」。
        """
        preset = PresetView(agent_ids=("finance_markets", "onchain_security"))
        result = _resolve(
            preset,
            agent_tool_selections={"finance_markets": ["technical_analysis"]},
        )
        assert "get_crypto_price" in result.tool_names
        assert any(
            "deselection not enforced" in w and "get_crypto_price" in w
            for w in result.warnings
        ), result.warnings

    def test_single_agent_deselection_does_not_warn(self):
        """單 agent preset 的勾選真的生效——不得誤報 shadowed warning。"""
        preset = PresetView(agent_ids=("onchain_security",))
        result = _resolve(
            preset, agent_tool_selections={"onchain_security": ["get_crypto_price"]}
        )
        assert not any("deselection not enforced" in w for w in result.warnings)

    def test_config_hash_changes_with_selection(self):
        preset = PresetView(agent_ids=("onchain_security",))
        a = _resolve(
            preset, agent_tool_selections={"onchain_security": ["get_crypto_price"]}
        )
        b = _resolve(
            preset,
            agent_tool_selections={"onchain_security": ["technical_analysis"]},
        )
        c = _resolve(preset)
        assert len({a.config_hash, b.config_hash, c.config_hash}) == 3

    def test_fail_closed_retry_ignores_selections(self):
        """池子全空觸發 fallback 時，retry 不套用使用者勾選（= 官方預設組合）。

        用 custom catalog 觀察：general_research 的 required_tools 留空
        （避免回補讓池子非空），與一個 category 對不到任何工具的 tiny profile
        組 team preset；勾選把池子清空 → retry 退回 general_research 預設池。
        """
        tmp = Path(tempfile.mkdtemp())
        (tmp / "general_research.yaml").write_text(
            textwrap.dedent(
                """\
                id: general_research
                version: "1.0.0"
                display_name: General Research
                allowed_tool_categories: [general]
                """
            ),
            encoding="utf-8",
        )
        (tmp / "tiny.yaml").write_text(
            textwrap.dedent(
                """\
                id: tiny
                version: "1.0.0"
                display_name: Tiny
                allowed_tool_categories: [nope_cat]
                """
            ),
            encoding="utf-8",
        )
        custom = load_catalog(tmp)
        preset = PresetView(agent_ids=("tiny", "general_research"))
        result = _resolve(
            preset,
            agent_tool_selections={"general_research": ["totally_unknown_tool"]},
            catalog=custom,
        )
        assert "web_search" in result.tool_names  # retry 用官方預設池，不被勾選縮掉
        assert any("fail-closed retry" in w for w in result.warnings)

    def test_resolve_preset_config_passes_selections_through(self):
        config = resolve_preset_config(
            {"agent_ids": ["onchain_security"], "action_policy": "read_only"},
            user_tier="free",
            agent_tool_selections={"onchain_security": ["get_crypto_price"]},
        )
        # onchain 的必要工具（安全判定）即使未勾選也會回補
        assert config["tool_names"] == [
            "check_address_safety",
            "check_token_security",
            "get_crypto_price",
        ]
        assert any("required tools re-added" in w for w in config["warnings"])

    def test_resolve_preset_config_selection_serializable(self):
        import json

        config = resolve_preset_config(
            {"agent_ids": ["general_research"]},
            user_tier="free",
            agent_tool_selections={"general_research": ["totally_unknown_tool"]},
        )
        json.dumps(config)
        assert config["tool_names"] == ["web_search"]
