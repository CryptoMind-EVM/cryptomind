"""Golden set tests for intent classification.

These tests validate the golden I/O pair structure. Actual agent invocation
requires LLM mocking per tests/AGENTS.md patterns — see test_entity_resolution_e2e.py
for reference. This file asserts the fixture loads correctly and shapes match
the IntentUnderstandingStructured schema (core/agents/models.py).
"""
import pytest


def _load_golden_set(name: str):
    """Helper to load golden cases directly (fixture-independent)."""
    import json
    from pathlib import Path

    path = Path(__file__).parent / "cases" / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Golden set not found: {name}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestIntentClassificationGoldenSet:
    """Validate golden I/O pairs are well-formed and match schema expectations."""

    @pytest.mark.unit
    def test_golden_set_loads(self):
        """All 3 cases must load with required fields."""
        cases = _load_golden_set("intent_classification")
        assert len(cases) >= 3, "Expected at least 3 golden cases"
        required_fields = {"id", "input", "expected"}
        for case in cases:
            assert required_fields.issubset(case.keys()), f"Case missing fields: {case}"
            assert "status" in case["expected"], f"Expected.status missing in {case['id']}"

    @pytest.mark.unit
    def test_crypto_price_case(self):
        """BTC price query — intent should classify to ready + crypto agent."""
        cases = _load_golden_set("intent_classification")
        case = next((c for c in cases if c["id"] == "crypto_price_btc"), None)
        assert case is not None, "Missing crypto_price_btc case"
        assert case["expected"]["status"] in ["ready", "direct_response"]
        assert case["expected"]["entities"].get("market") == "crypto"
        assert any(t.get("agent") == "crypto" for t in case["expected"].get("tasks", []))

    @pytest.mark.unit
    def test_clarification_case(self):
        """Vague query — should return clarify status with a question."""
        cases = _load_golden_set("intent_classification")
        case = next((c for c in cases if c["id"] == "vague_query"), None)
        assert case is not None, "Missing vague_query case"
        assert case["expected"]["status"] == "clarify"
        assert case["expected"].get("clarification_question")

    @pytest.mark.unit
    def test_multi_task_case(self):
        """Multi-intent query — should decompose to >=2 tasks."""
        cases = _load_golden_set("intent_classification")
        case = next((c for c in cases if c["id"] == "multi_task_analysis"), None)
        assert case is not None, "Missing multi_task_analysis case"
        assert case["expected"]["status"] == "ready"
        assert len(case["expected"]["tasks"]) >= 2
        agents = {t["agent"] for t in case["expected"]["tasks"]}
        assert "crypto" in agents
        assert "news" in agents
