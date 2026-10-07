"""Tests for agent self-managed skills/memory tools + HITL consent markers.

docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md

Covers:
- list_my_skills_memory (read-only, per-user isolation, graceful no-user)
- propose_custom_skill (marker shape, PII block, validation, modes)
- remember (now returns marker, not direct write — PII still pre-blocked)
- _extract_consent_signal (marker detection from tool outputs)
- parse_skill_memory_consent_answer (fail-closed)
- _apply_consent_write (write only after approval; user_id from node, not marker)

Patterns follow test_remember_tool.py (contextvar user_id) and
test_consent_gate.py (@patch audit_log, GraphInterrupt ordering).
"""
import json

import pytest

pytestmark = pytest.mark.unit


# ── list_my_skills_memory ──────────────────────────────────────────────

class TestListMySkillsMemory:
    def test_no_user_returns_graceful_message(self):
        from core.tools.key_resolver import set_current_user_id

        set_current_user_id(None)
        from core.tools.skill_memory_tools import list_my_skills_memory

        result = list_my_skills_memory.invoke({"kind": "all"})
        assert isinstance(result, str)
        assert "No logged-in user" in result

    def test_returns_string_for_llm_not_marker(self):
        """list is read-only → returns plain text for the LLM, never a marker."""
        from core.tools.key_resolver import set_current_user_id

        set_current_user_id("test-list-user")
        from core.tools.skill_memory_tools import list_my_skills_memory

        result = list_my_skills_memory.invoke({"kind": "all"})
        assert isinstance(result, str)
        assert "__needs_consent__" not in result  # not a write marker

    def test_invalid_kind_normalized_to_all(self):
        from core.tools.key_resolver import set_current_user_id

        set_current_user_id("test-list-user")
        from core.tools.skill_memory_tools import list_my_skills_memory

        # bogus kind shouldn't crash — falls back to all
        result = list_my_skills_memory.invoke({"kind": "nonsense"})
        assert isinstance(result, str)


class TestListToolRegistration:
    def test_list_tool_registered_regardless_of_self_manage_flag(self):
        """2026-09-14 根絕「我沒有記憶」幻覺：唯讀清單工具必須無條件註冊。

        之前它被 AGENT_SELF_MANAGE_ENABLED（預設 false）擋住——旗標關閉時
        模型無法自查記憶，被問「有記住嗎」只能憑感覺答「我沒有長期記憶」。
        寫入路徑（propose_custom_skill）仍受旗標控制。
        """
        from unittest.mock import MagicMock

        from core.agents.bootstrap import (
            bootstrap,
            invalidate_manager_cache,
        )

        user_id = "reg-test-user"
        invalidate_manager_cache(user_id)
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="ok")
        llm.ainvoke = MagicMock(return_value=MagicMock(content="ok"))
        manager = bootstrap(
            llm, web_mode=False, user_id=user_id, session_id="reg-test-session"
        )
        try:
            assert (
                manager.tool_registry.get(
                    "list_my_skills_memory", caller_agent="cryptomind"
                )
                is not None
            )
            # 看 bootstrap 開機時實際讀到的值（config 模組可能被其他測試 reload 過）
            from core.agents.bootstrap import AGENT_SELF_MANAGE_ENABLED

            if not AGENT_SELF_MANAGE_ENABLED:
                assert (
                    manager.tool_registry.get(
                        "propose_custom_skill", caller_agent="cryptomind"
                    )
                    is None
                )
        finally:
            invalidate_manager_cache(user_id)


# ── propose_custom_skill ───────────────────────────────────────────────

class TestProposeCustomSkill:
    def test_create_returns_consent_marker(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "mode": "create",
            "skill_name": "india-stocks",
            "description": "India stock fundamental analysis",
            "trigger_keywords": "india,NIFTY,SENSEX",
            "body": "1. Check NIFTY 50 trend\n2. FII/DII flows",
            "reason": "You ask about India stocks often",
        })
        parsed = json.loads(result)
        assert parsed["__needs_consent__"] is True
        assert parsed["kind"] == "custom_skill"
        assert parsed["mode"] == "create"
        assert parsed["skill_name"] == "india-stocks"
        assert parsed["reason"] == "You ask about India stocks often"

    def test_marker_has_no_user_id_field(self):
        """Safety: marker must NOT carry user_id (node reads it from session)."""
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "skill_name": "test-skill",
            "description": "d",
            "body": "b",
        })
        parsed = json.loads(result)
        assert "user_id" not in parsed

    def test_invalid_name_rejected(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "skill_name": "bad name with spaces!",
            "description": "d",
            "body": "b",
        })
        assert "⚠️" in result
        assert "Invalid skill_name" in result

    def test_pii_in_body_blocked_before_marker(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "skill_name": "leak",
            "description": "d",
            "body": "My private key is -----BEGIN RSA PRIVATE KEY-----",
        })
        assert "⚠️" in result
        assert "Sensitive information" in result
        # must NOT produce a marker
        assert "__needs_consent__" not in result

    def test_create_requires_description_and_body(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "skill_name": "incomplete",
            "description": "",
            "body": "",
        })
        assert "required" in result.lower()

    def test_body_too_long_rejected(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "skill_name": "big",
            "description": "d",
            "body": "x" * 3001,
        })
        assert "too long" in result

    def test_update_mode_marker(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "mode": "update",
            "skill_name": "existing",
            "description": "new desc",
            "body": "new body",
        })
        parsed = json.loads(result)
        assert parsed["mode"] == "update"

    def test_delete_mode_marker(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "mode": "delete",
            "skill_name": "unwanted",
        })
        parsed = json.loads(result)
        assert parsed["mode"] == "delete"

    def test_invalid_mode_rejected(self):
        from core.tools.skill_memory_tools import propose_custom_skill

        result = propose_custom_skill.invoke({
            "mode": "explode",
            "skill_name": "x",
        })
        assert "Invalid mode" in result


# ── remember (now marker-based) ────────────────────────────────────────

class TestRememberMarker:
    def test_remember_returns_consent_marker_not_write(self):
        """remember no longer writes directly — it returns a consent marker."""
        from core.tools.key_resolver import set_current_user_id

        set_current_user_id("test-remember-user")
        from core.tools.remember_tool import remember

        result = remember.invoke({"content": "I prefer technical analysis", "category": "preference"})
        parsed = json.loads(result)
        assert parsed["__needs_consent__"] is True
        assert parsed["kind"] == "create_memory"
        assert parsed["category"] == "preference"

    def test_pii_still_blocked_before_marker(self):
        from core.tools.key_resolver import set_current_user_id

        set_current_user_id("test-remember-user")
        from core.tools.remember_tool import remember

        result = remember.invoke({"content": "身分證 A123456789", "category": "fact"})
        assert "⚠️" in result
        assert "__needs_consent__" not in result

    def test_category_normalized(self):
        from core.tools.key_resolver import set_current_user_id

        set_current_user_id("test-remember-user")
        from core.tools.remember_tool import remember

        result = remember.invoke({"content": "test fact", "category": "BOGUS"})
        parsed = json.loads(result)
        assert parsed["category"] == "fact"  # invalid → fact


# ── _extract_consent_signal ────────────────────────────────────────────

class TestExtractConsentSignal:
    def test_detects_marker_in_tool_outputs(self):
        from core.agents.manager.claw_loop import _extract_consent_signal

        data = {
            "tool_outputs": [
                "some normal output",
                json.dumps({"__needs_consent__": True, "kind": "create_memory",
                            "content": "x", "category": "fact"}),
            ]
        }
        signal = _extract_consent_signal(data)
        assert signal is not None
        assert signal["kind"] == "create_memory"

    def test_returns_none_when_no_marker(self):
        from core.agents.manager.claw_loop import _extract_consent_signal

        data = {"tool_outputs": ["plain text", "more text"]}
        assert _extract_consent_signal(data) is None

    def test_returns_none_empty(self):
        from core.agents.manager.claw_loop import _extract_consent_signal

        assert _extract_consent_signal({}) is None
        assert _extract_consent_signal({"tool_outputs": []}) is None
        assert _extract_consent_signal(None) is None  # type: ignore[arg-type]

    def test_ignores_non_json_strings(self):
        from core.agents.manager.claw_loop import _extract_consent_signal

        data = {"tool_outputs": ["not json { at all", "{bad json"]}
        assert _extract_consent_signal(data) is None


# ── parse_skill_memory_consent_answer ──────────────────────────────────

class TestParseConsentAnswer:
    def test_approve_skill(self):
        from core.agents.manager.consent_gate import parse_skill_memory_consent_answer

        ans = {"action": "skill_consent", "approved": True}
        parsed = parse_skill_memory_consent_answer(ans)
        assert parsed["approved"] is True

    def test_decline_skill(self):
        from core.agents.manager.consent_gate import parse_skill_memory_consent_answer

        ans = {"action": "skill_consent", "approved": False}
        parsed = parse_skill_memory_consent_answer(ans)
        assert parsed["approved"] is False

    def test_edited_fields_preserved(self):
        from core.agents.manager.consent_gate import parse_skill_memory_consent_answer

        ans = {"action": "skill_consent", "approved": True,
               "edited_fields": {"description": "user-edited"}}
        parsed = parse_skill_memory_consent_answer(ans)
        assert parsed["edited_fields"]["description"] == "user-edited"

    def test_cancel_action_fail_closed(self):
        from core.agents.manager.consent_gate import parse_skill_memory_consent_answer

        parsed = parse_skill_memory_consent_answer({"action": "cancel"})
        assert parsed["approved"] is False

    def test_unparseable_fail_closed(self):
        from core.agents.manager.consent_gate import parse_skill_memory_consent_answer

        assert parse_skill_memory_consent_answer(None)["approved"] is False
        assert parse_skill_memory_consent_answer("garbage")["approved"] is False
        assert parse_skill_memory_consent_answer({})["approved"] is False


# ── _apply_consent_write ───────────────────────────────────────────────

class TestApplyConsentWrite:
    def test_no_user_id_skips_write(self):
        from core.agents.manager.claw_loop import _apply_consent_write

        # should not raise, just log warning
        _apply_consent_write(None, {"kind": "create_memory", "content": "x"}, {})

    @pytest.mark.asyncio
    async def test_memory_write_calls_store(self):
        from core.agents.manager.claw_loop import _apply_consent_write

        signal = {"kind": "create_memory", "content": "I like crypto",
                  "category": "preference", "key": "pref_abc123"}
        with pytest.MonkeyPatch.context() as mp:
            written = []

            class FakeStore:
                def __init__(self, *a, **kw):
                    pass

                def write_facts(self, facts):
                    written.extend(facts)

            mp.setattr("core.database.memory.MemoryStore", FakeStore)
            _apply_consent_write("user-1", signal, {})
            assert len(written) == 1
            assert written[0]["value"] == "I like crypto"

    @pytest.mark.asyncio
    async def test_skill_create_calls_store(self):
        from core.agents.manager.claw_loop import _apply_consent_write

        signal = {"kind": "custom_skill", "mode": "create",
                  "skill_name": "test", "description": "d",
                  "trigger_keywords": "", "body": "b"}
        with pytest.MonkeyPatch.context() as mp:
            created = []

            class FakeStore:
                def __init__(self, *a, **kw):
                    pass

                def create_custom_skill(self, **kw):
                    created.append(kw)
                    return {"ok": True}

            mp.setattr("core.database.skill_preferences.SkillPreferenceStore", FakeStore)
            _apply_consent_write("user-1", signal, {})
            assert len(created) == 1
            assert created[0]["skill_name"] == "test"

    def test_edited_fields_override_signal(self):
        """User 'edit before approve' must override the agent's proposed content."""
        from core.agents.manager.claw_loop import _apply_consent_write

        signal = {"kind": "create_memory", "content": "agent version",
                  "category": "fact", "key": "k1"}
        edited = {"content": "user edited version"}
        with pytest.MonkeyPatch.context() as mp:
            written = []

            class FakeStore:
                def __init__(self, *a, **kw):
                    pass

                def write_facts(self, facts):
                    written.extend(facts)

            mp.setattr("core.database.memory.MemoryStore", FakeStore)
            _apply_consent_write("user-1", signal, edited)
            assert written[0]["value"] == "user edited version"


# ── GraphInterrupt not swallowed ───────────────────────────────────────

class TestInterruptPropagation:
    def test_graph_interrupt_not_swallowed_as_fault(self):
        """The consent phase's except ordering must let GraphInterrupt propagate
        (same invariant as Phase D/E/F consent/clarify)."""
        import asyncio

        from langgraph.errors import GraphInterrupt

        fault_logged = False

        def simulate_consent_block():
            nonlocal fault_logged
            try:
                raise GraphInterrupt(("skill_consent", {}))
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except GraphInterrupt:
                raise  # MUST re-raise, not swallow
            except Exception:
                fault_logged = True

        raised = False
        try:
            simulate_consent_block()
        except GraphInterrupt:
            raised = True

        assert raised is True
        assert fault_logged is False
