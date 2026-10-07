"""全面盤查（2026-09-25）AI 層批：checkpointer 改 LRU、工具的 user_id 只認登入身分。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


class TestCheckpointerLru:
    def test_recently_used_session_survives_eviction(self, monkeypatch):
        from core.agents.manager import _main

        monkeypatch.setattr(_main, "_per_user_checkpointer", {})
        monkeypatch.setattr(_main, "_CHECKPOINTER_MAX", 3)
        waiting = _main._get_checkpointer("u1", "s-waiting-hitl")
        _main._get_checkpointer("u2", "s2")
        _main._get_checkpointer("u3", "s3")
        # 使用者回答 HITL → resume 會再取一次，這要讓它變成「最近用過」
        assert _main._get_checkpointer("u1", "s-waiting-hitl") is waiting
        _main._get_checkpointer("u4", "s4")  # 超過上限 → 淘汰最久沒用的 u2
        assert _main._get_checkpointer("u1", "s-waiting-hitl") is waiting
        assert "u2:s2" not in _main._per_user_checkpointer
        assert len(_main._per_user_checkpointer) == 3


class TestUserIdFromIdentityOnly:
    def test_task_context_cannot_override_user(self):
        from core.agents.base_react_agent import BaseReActAgent

        class Dummy(BaseReActAgent):
            @property
            def name(self) -> str:
                return "dummy"

        agent = Dummy(llm_client=MagicMock(), tool_registry=MagicMock(), user_id="0xme")
        task = MagicMock()
        task.context = {"user_id": "0xvictim", "user_tier": "free"}
        _, user_id = agent._resolve_user_scope(task)
        assert user_id == "0xme"
