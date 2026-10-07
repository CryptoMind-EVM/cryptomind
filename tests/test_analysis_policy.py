from core.agents.analysis_policy import AnalysisPolicyResolver


def test_analysis_policy_builds_price_lookup_from_config():
    resolver = AnalysisPolicyResolver()

    profile = resolver.build_query_profile("AAPL 現在多少？", ["AAPL"])

    assert profile["query_type"] == "price_lookup"
    assert profile["has_symbol_candidates"] is True


def test_analysis_policy_uses_unified_discovery_rule_from_config():
    resolver = AnalysisPolicyResolver()

    assert "modes" not in resolver.config

    decision = resolver.resolve(
        {
            "metadata": {
                "market_resolution": {
                    "requires_discovery_lookup": True,
                },
                "query_profile": {
                    "query_type": "price_lookup",
                },
            },
        }
    )

    assert decision.required_tool_role == "discovery_lookup"
    assert decision.fail_reason == "discovery_tool_unavailable"


def test_analysis_policy_uses_unified_market_data_rule_when_grounding_is_required():
    resolver = AnalysisPolicyResolver()

    decision = resolver.resolve(
        {
            "metadata": {
                "market_resolution": {
                    "requires_discovery_lookup": False,
                },
                "query_profile": {
                    "query_type": "price_lookup",
                },
            },
            "tool_required": True,
        }
    )

    assert decision.required_tool_role == "market_lookup"
    assert decision.fail_reason == "tool_unavailable"
