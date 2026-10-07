"""Step 4/5/6 code review 找到的靜默失效——先紅後綠的回歸測試。

四個問題的共同形狀：**都不噴錯**。壞掉時看起來像功能沒觸發，
而不是像 bug，所以只有把契約釘在測試裡才擋得住。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


class TestForkSignalEscapesAgent:
    """loop_fork 的觸發訊號必須逃得出 agent 的 except Exception。"""

    async def test_fork_request_propagates_out_of_execute_streaming(self):
        """`_on_tool_start` raise 的分岔訊號被 execute_streaming 吞掉的話，
        claw_loop 的 ``except _ForkPointRequested`` 永遠不會執行——
        使用者拿到的不是分岔卡，而是一則錯誤訊息。"""
        from langchain_core.messages import AIMessageChunk

        from core.agents.base_react_agent import BaseReActAgent
        from core.agents.fork_signal import ForkPointRequested
        from core.agents.models import SubTask

        class _Dummy(BaseReActAgent):
            @property
            def name(self) -> str:
                return "cryptomind"

        class _FakeCompiled:
            async def astream(self, _inp, stream_mode=None):
                yield (
                    "messages",
                    (
                        AIMessageChunk(
                            content="",
                            tool_call_chunks=[
                                {
                                    "name": "web_search",
                                    "args": "",
                                    "id": "call_1",
                                    "index": 0,
                                }
                            ],
                        ),
                        {},
                    ),
                )

        agent = _Dummy(
            llm_client=None, tool_registry=None, user_tier="free", user_id="u1"
        )
        task = SubTask(
            step=1, description="q", agent="cryptomind", context={"language": "en"}
        )

        def _on_tool_start(_name):
            raise ForkPointRequested(15, ["web_search"])

        with patch.object(_Dummy, "_get_tool_metas", return_value=[]), patch.object(
            _Dummy, "_build_agent_system_prompt", return_value="sys"
        ), patch(
            "core.agents.base_react_agent.create_agent", return_value=_FakeCompiled()
        ):
            with pytest.raises(ForkPointRequested):
                await agent.execute_streaming(task, on_tool_start=_on_tool_start)

    def test_claw_loop_alias_is_the_shared_signal(self):
        """claw_loop 的名稱必須就是共用訊號本身，否則 except 對不上。"""
        from core.agents.fork_signal import ForkPointRequested
        from core.agents.manager.claw_loop import _ForkPointRequested

        assert _ForkPointRequested is ForkPointRequested


class TestWorkerResumeKeepsUpdate:
    """resume 輪帶的 state 更新，worker 路徑要跟同步路徑一樣保留。"""

    def test_job_envelope_keeps_resume_update(self):
        from langgraph.types import Command

        from api.routers.analysis import _build_job_envelope

        env = _build_job_envelope(
            run_id="r1",
            session_id="s1",
            user_id="u1",
            language="zh-TW",
            user_tier="premium",
            display_name=None,
            wallet_address=None,
            credentials={},
            key_fingerprint="",
            web_mode=True,
            graph_input=Command(resume={"ok": True}, update={"llm_model": "m1"}),
            config={},
            resume_answer={"ok": True},
            llm_selection=None,
            user_model_preference=None,
        )
        assert env["graph_input"]["update"]["llm_model"] == "m1"

    def test_worker_rebuilds_resume_command_with_update(self):
        from scripts.analysis_worker import _rebuild_graph_input

        cmd = _rebuild_graph_input(
            {
                "graph_input": {"resume": {"ok": True}, "update": {"llm_model": "m1"}},
                "resume_answer": {"ok": True},
            }
        )
        assert cmd.resume == {"ok": True}
        assert (cmd.update or {}).get("llm_model") == "m1"


class TestFactsPromptCap:
    """facts_to_text 的上限不能因為分區而變成兩倍。"""

    def test_total_facts_still_capped(self):
        from core.database.memory import MAX_FACTS_IN_PROMPT, MemoryStore

        store = MemoryStore(user_id="u1", agent_id="finance_markets")
        facts = {}
        for i in range(MAX_FACTS_IN_PROMPT):
            facts[f"s{i}"] = {"value": f"shared {i}", "agent_id": None}
        for i in range(MAX_FACTS_IN_PROMPT):
            facts[f"p{i}"] = {"value": f"private {i}", "agent_id": "finance_markets"}

        with patch.object(MemoryStore, "read_facts", return_value=facts):
            text = store.facts_to_text()
        bullets = [ln for ln in text.splitlines() if ln.startswith("- ")]
        assert len(bullets) <= MAX_FACTS_IN_PROMPT, (
            f"分區渲染讓 prompt 事實上限翻倍：{len(bullets)}"
        )


class TestLoopForkCardReachesThePrimaryStream:
    """分岔卡只掛在 resume 串流上＝實際上永遠不會出現。"""

    @staticmethod
    def _strip_comments(src: str) -> str:
        """拿掉 // 與 /* */ 註解——否則守衛會被自己的說明文字滿足。"""
        import re

        src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", "", src, flags=re.MULTILINE)

    def _js(self, name: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        # encoding 必須明講：前端原始碼是 UTF-8，Windows 的預設 locale（cp950）
        # 讀到中文註解／字串就 UnicodeDecodeError。
        return self._strip_comments((root / "web" / "js" / name).read_text(encoding="utf-8"))

    def test_card_is_exported_to_window(self):
        src = self._js("chat-hitl.js")
        assert "window.renderLoopForkCard = renderLoopForkCard" in src

    def test_primary_send_stream_dispatches_loop_fork(self):
        """軟門檻在**第一輪**觸發，走的是 chat-analysis 的 sendMessage 串流。"""
        src = self._js("chat-analysis.js")
        assert "idata.type === 'loop_fork'" in src, (
            "chat-analysis.js 沒有 loop_fork 分支——分岔卡會掉進 clarify fallback"
        )
        assert "window.renderLoopForkCard(idata, botMsgDiv)" in src
