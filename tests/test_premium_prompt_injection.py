"""Premium system_prompt / enabled_tools 注入測試。

驗證「最後一哩」接線：claw_loop 把 state 的 system_prompt / enabled_tools 搬進
SubTask.context 後，base_react_agent 真的讀到並生效。

對齊 test_claw_hardening.py 風格：用 CryptoMindAgent.__new__() 跳過 __init__，
直接測 _build_agent_system_prompt（base 類方法，cryptomind 沒覆寫）與
_filter_tool_metas（base 類方法）。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from core.agents.models import SubTask

# ============================================================================
# Helper：建一個最小可用的 agent（跳過 __init__）
# ============================================================================


def _make_agent():
    """建 CryptoMindAgent 但跳過 __init__，只設測試需要的屬性。"""
    from core.agents.agents.cryptomind_agent import CryptoMindAgent
    from core.agents.prompt_registry import PromptRegistry

    PromptRegistry.load()
    agent = CryptoMindAgent.__new__(CryptoMindAgent)

    class _FakeNemotronLLM:
        model_name = "nvidia/nemotron-3-super-120b-a12b"

    agent.llm = _FakeNemotronLLM()
    # _build_agent_system_prompt → _load_skill_preferences 會讀 user_id
    #（跳過 __init__ 不會有這屬性——與 _make_agent_with_tools 一致）。
    agent.user_id = "test-user"
    return agent


def _make_task(**context_kwargs):
    """建一個帶 context 的 SubTask。"""
    return SubTask(
        step=0,
        description="test",
        agent="cryptomind",
        context=context_kwargs,
    )


# ============================================================================
# A. system_prompt 注入
# ============================================================================


class TestSystemPromptInjection:
    def test_system_prompt_appended_to_system_message(self):
        """context 帶 system_prompt → 輸出含該字串 + 「使用者自訂指示」標題。"""
        agent = _make_agent()
        task = _make_task(system_prompt="一律用繁體中文回答")

        prompt = agent._build_agent_system_prompt(task, "zh-TW")

        assert "使用者自訂指示" in prompt
        assert "一律用繁體中文回答" in prompt

    def test_system_prompt_none_does_not_inject(self):
        """context 沒帶 system_prompt → 不含「使用者自訂指示」區塊。"""
        agent = _make_agent()
        task = _make_task()  # 沒帶 system_prompt

        prompt = agent._build_agent_system_prompt(task, "zh-TW")

        assert "使用者自訂指示" not in prompt

    def test_system_prompt_empty_string_does_not_inject(self):
        """system_prompt 為空字串 → 視為無，不注入（避免空區塊）。"""
        agent = _make_agent()
        task = _make_task(system_prompt="")

        prompt = agent._build_agent_system_prompt(task, "zh-TW")

        assert "使用者自訂指示" not in prompt

    def test_system_prompt_placed_after_safety_protocol(self):
        """自訂指示必須在 base role / safety protocol 之後（不能覆蓋）。

        CryptoMindAgent._get_system_prompt 會注入 base role 等協議；
        使用者自訂只能 append 在後。用「base role 出現位置 < 自訂指示位置」驗證。
        """
        agent = _make_agent()
        task = _make_task(system_prompt="MY_CUSTOM_MARKER")

        prompt = agent._build_agent_system_prompt(task, "zh-TW")

        # 找一個 base protocol 應該有的標記（CryptoMind 一定有「分析」或 role 描述）
        # 這裡用「使用者自訂指示」標題位置 vs prompt 開頭的 base role
        custom_idx = prompt.index("使用者自訂指示")
        # 自訂指示不該出現在 prompt 最前面（base role 要先）
        assert custom_idx > 0, "自訂指示不該在 prompt 開頭（必須晚於 safety protocol）"
        # base role 的某個關鍵字應該在自訂指示之前
        # CryptoMindAgent._get_system_prompt 開頭是時間錨 + 身份，必然有內容
        assert len(prompt[:custom_idx].strip()) > 0, "自訂指示前應有 base protocol"

    def test_system_prompt_before_memory_and_experience(self):
        """自訂指示應在 memory/experience 之前（順序：protocol > 自訂 > memory > experience）。"""
        agent = _make_agent()
        task = _make_task(
            system_prompt="MY_CUSTOM",
            memory_context="MY_MEMORY",
            experience_hint="MY_EXPERIENCE",
        )

        prompt = agent._build_agent_system_prompt(task, "zh-TW")

        custom_idx = prompt.index("MY_CUSTOM")
        memory_idx = prompt.index("MY_MEMORY")
        exp_idx = prompt.index("MY_EXPERIENCE")
        assert custom_idx < memory_idx < exp_idx, (
            f"順序錯: custom={custom_idx}, memory={memory_idx}, exp={exp_idx}"
        )


# ============================================================================
# B. enabled_tools 交集限縮
# ============================================================================


@dataclass
class _FakeToolMeta:
    """極簡 ToolMetadata 替身（只測 name 比對）。"""

    name: str


class TestEnabledToolsIntersection:
    """enabled_tools 走交集限縮：只能在 get_allowed_tools 結果上再縮窄。"""

    def _make_agent_with_tools(self, tool_names):
        """建 agent，tool_registry.list_for_agent 回傳指定工具的 fake meta。"""
        from core.agents.agents.cryptomind_agent import CryptoMindAgent
        from core.agents.prompt_registry import PromptRegistry

        PromptRegistry.load()
        agent = CryptoMindAgent.__new__(CryptoMindAgent)
        agent.llm = type("FakeLLM", (), {"model_name": "nemotron"})()
        # _resolve_user_scope 會讀這兩個屬性
        agent.user_tier = "free"
        agent.user_id = "test-user"

        fake_registry = type(
            "FakeRegistry",
            (),
            {
                "list_for_agent": lambda self, name: [
                    _FakeToolMeta(n) for n in tool_names
                ]
            },
        )()
        agent.tool_registry = fake_registry
        return agent

    def test_intersects_with_allowed_tools(self):
        """allowed={A,B,C}, enabled=[A,B] → 結果 {A,B}。"""
        agent = self._make_agent_with_tools(["A", "B", "C"])
        task = _make_task(enabled_tools=["A", "B"])

        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["A", "B", "C"],
        ):
            result = agent._filter_tool_metas(task)

        names = {m.name for m in result}
        assert names == {"A", "B"}

    def test_cannot_unlock_disabled_tool(self):
        """allowed={A}（B 被關掉）, enabled=[A,B] → 結果 {A}，B 解鎖不了。"""
        agent = self._make_agent_with_tools(["A", "B"])
        task = _make_task(enabled_tools=["A", "B"])

        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["A"],  # B 被 user_tool_preferences 關掉
        ):
            result = agent._filter_tool_metas(task)

        names = {m.name for m in result}
        assert names == {"A"}, f"B 不該被解鎖: {names}"

    def test_empty_enabled_does_not_filter(self):
        """enabled=[] → 不限縮，回 allowed 全集。"""
        agent = self._make_agent_with_tools(["A", "B", "C"])
        task = _make_task(enabled_tools=[])

        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["A", "B", "C"],
        ):
            result = agent._filter_tool_metas(task)

        names = {m.name for m in result}
        assert names == {"A", "B", "C"}, "空 enabled 不該限縮"

    def test_none_enabled_does_not_filter(self):
        """context 沒帶 enabled_tools → 不限縮。"""
        agent = self._make_agent_with_tools(["A", "B"])
        task = _make_task()  # 沒帶 enabled_tools

        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["A", "B"],
        ):
            result = agent._filter_tool_metas(task)

        names = {m.name for m in result}
        assert names == {"A", "B"}


# ============================================================================
# C. claw_loop SubTask.context 帶這兩鍵（接線確認）
# ============================================================================


class TestSubTaskContextWiring:
    """確認 claw_loop 組 SubTask 時真的把 state 的值搬進 context。

    用字串 grep source 的方式驗證接線存在（避免觸發完整 graph）。
    """

    def test_claw_loop_source_carries_system_prompt(self):
        """claw_loop.py source 必須把 state.system_prompt 搬進 SubTask.context。"""
        import inspect

        from core.agents.manager import claw_loop

        src = inspect.getsource(claw_loop)
        # 必須在 SubTask 構造處讀 state.system_prompt
        assert '"system_prompt": state.get("system_prompt")' in src, (
            "claw_loop 必須把 state.system_prompt 搬進 SubTask.context"
        )

    def test_claw_loop_source_carries_enabled_tools(self):
        """claw_loop.py source 必須把 state.enabled_tools 搬進 SubTask.context。"""
        import inspect

        from core.agents.manager import claw_loop

        src = inspect.getsource(claw_loop)
        assert '"enabled_tools": state.get("enabled_tools")' in src, (
            "claw_loop 必須把 state.enabled_tools 搬進 SubTask.context"
        )
