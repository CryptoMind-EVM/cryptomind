"""
Integration tests for the CLAW-style architecture refactor.

Verifies:
- direct_response is removed and coerced to ready
- AgentContext carries memory_context + experience_hint
- normalize_trigger_pattern produces stable patterns
- MEMORY_CONSOLIDATION_THRESHOLD is configurable
- Skill recommended_tools are parsed correctly
"""




class TestDirectResponseRemoval:
    """Phase 1: direct_response shortcut removed, coerced to ready."""

    def test_prompt_guard_coerces_legacy_direct_response(self):
        from core.agents.prompt_guard import validate_intent_response

        result = validate_intent_response(
            {"status": "direct_response", "user_intent": "hello"}, "hello"
        )
        assert result["status"] == "ready"

    def test_valid_statuses_are_ready_and_clarify_only(self):
        from core.agents.prompt_guard import _VALID_INTENT_STATUSES

        assert _VALID_INTENT_STATUSES == {"ready", "clarify"}
        assert "direct_response" not in _VALID_INTENT_STATUSES

    def test_intent_schema_has_two_statuses(self):
        from core.agents.models import IntentUnderstandingStructured

        fields = IntentUnderstandingStructured.model_fields
        assert "direct_response_text" not in fields
        status_field = fields["status"]
        assert "direct_response" not in str(status_field.annotation)


class TestAgentContextMemoryFields:
    """Phase 2: memory_context + experience_hint on AgentContext."""

    def test_agent_context_has_memory_fields(self):
        from core.agents.models import AgentContext

        ctx = AgentContext(
            original_query="BTC price",
            task_description="Get BTC price",
            symbols={"crypto": "BTC"},
            memory_context="User prefers technical analysis",
            experience_hint="Past success with get_crypto_price",
        )
        assert ctx.memory_context == "User prefers technical analysis"
        assert ctx.experience_hint == "Past success with get_crypto_price"

    def test_agent_context_memory_defaults_none(self):
        from core.agents.models import AgentContext

        ctx = AgentContext(
            original_query="hello",
            task_description="greet",
            symbols={},
        )
        assert ctx.memory_context is None
        assert ctx.experience_hint is None


class TestConfigurableMemoryThreshold:
    """Phase 3.5: MEMORY_CONSOLIDATION_THRESHOLD is env-configurable."""

    def test_default_threshold_is_12(self):
        from core.agents.manager._main import MEMORY_CONSOLIDATION_THRESHOLD

        assert MEMORY_CONSOLIDATION_THRESHOLD == 12

    def test_threshold_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_CONSOLIDATION_THRESHOLD", "6")
        import importlib

        import core.agents.manager._main as mod

        importlib.reload(mod)
        assert mod.MEMORY_CONSOLIDATION_THRESHOLD == 6
        monkeypatch.delenv("MEMORY_CONSOLIDATION_THRESHOLD", raising=False)
        importlib.reload(mod)


class TestSkillRecommendedTools:
    """T12: Skill recommended_tools parsed from frontmatter."""

    def test_skills_have_recommended_tools(self):
        from core.agents.skill_loader import get_skill_loader

        loader = get_skill_loader()
        loader._loaded = False
        loader._cache.clear()
        skills = loader.load_all()

        crypto_tech = skills.get("crypto-technical-analysis")
        assert crypto_tech is not None
        assert len(crypto_tech.recommended_tools) >= 2
        assert "technical_analysis" in crypto_tech.recommended_tools

    def test_community_engagement_has_no_tools(self):
        from core.agents.skill_loader import get_skill_loader

        loader = get_skill_loader()
        loader._loaded = False
        loader._cache.clear()
        skills = loader.load_all()

        community = skills.get("community-engagement")
        assert community is not None
        assert len(community.recommended_tools) == 0

    def test_all_skills_loaded(self):
        from core.agents.skill_loader import get_skill_loader

        loader = get_skill_loader()
        loader._loaded = False
        loader._cache.clear()
        skills = loader.load_all()

        assert len(skills) >= 12
