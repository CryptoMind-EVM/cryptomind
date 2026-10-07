"""Unit tests for CLAW 直通模式（單迴圈）。

背景（DANNY 決策）：轉成 claw 形式後，舊的 5 節點管線
（intent 分解 / aggregate / reflect / synthesize）成為幻覺放大器——
合成層看不到工具原始輸出，只看到 sub-agent 文字摘要，於是在
豐富格式壓力下腦補（FLOW 假安全事件事故）。

參照 hermes-agent / OpenClaw 的單迴圈設計：
- 寫最終答案的模型與看到工具輸出的模型同一個 context
- 一題一條 LLM 迴圈，不再有第二次改寫

Covers:
- 直通模式圖只有 claw_loop 單節點（舊 5 節點管線已完全移除）
- _claw_loop_node 正常回傳 final_response 並記錄 used_tools
- execute_streaming 從 astream 事件流組出最終回覆與 used_tools
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.manager._main import ManagerAgent
from core.agents.models import AgentResult

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Graph shape — CLAW direct mode is now the only architecture.
# The 5-node pipeline (understand_intent / execute_task / aggregate_results /
# reflect_on_results / synthesize_response) has been removed entirely.
# ---------------------------------------------------------------------------


def _make_manager():
    return ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="test-user",
        session_id="test-session",
    )


def test_graph_is_single_claw_loop_node():
    manager = _make_manager()
    nodes = set(manager.graph.get_graph().nodes.keys())
    assert "claw_loop" in nodes
    # Legacy nodes must be gone — no fallback path exists anymore.
    assert "synthesize_response" not in nodes
    assert "understand_intent" not in nodes
    assert "execute_task" not in nodes
    assert "reflect_on_results" not in nodes
    assert "aggregate_results" not in nodes


# ---------------------------------------------------------------------------
# _claw_loop_node
# ---------------------------------------------------------------------------


def test_claw_loop_node_returns_final_response():
    manager = _make_manager()

    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(
        return_value=AgentResult(
            success=True,
            message="FLOW 現價 $0.0271（截至 2026-07-12）",
            agent_name="cryptomind",
            data={"used_tools": ["get_crypto_price"]},
        )
    )
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "FLOW 現在多少錢", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))

    assert "0.0271" in result["final_response"]
    assert result["_processed_query"] == "FLOW 現在多少錢"
    manager._track_conversation.assert_awaited_once()
    tools_used = manager._track_conversation.await_args.kwargs["tools_used"]
    assert tools_used == ["get_crypto_price"]
    # sub-agent 收到的 description 必須含時間錨定
    task = fake_agent.execute_streaming.await_args.args[0]
    assert "[REF:" in task.description


def test_claw_loop_node_handles_agent_failure():
    manager = _make_manager()

    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(side_effect=RuntimeError("boom"))
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "hi", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))
    assert result["final_response"]  # 有友善 fallback，不會炸
    assert "稍後再試" in result["final_response"]


@pytest.mark.parametrize("enabled", [False, True])
async def test_consent_granted_node_still_emits_final_metrics(enabled):
    manager = _make_manager()
    agent = MagicMock()
    agent.execute_streaming = AsyncMock(return_value=AgentResult(
        success=True, message="FLOW 現價 $0.0271", agent_name="cryptomind",
        data={"used_tools": ["get_crypto_price"]},
    ))
    manager.agent_registry.get.return_value = agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)
    with patch("core.agents.manager.claw_loop.router_enabled", return_value=enabled), \
         patch("core.agents.manager.claw_loop.log_run_metrics") as metrics:
        result = await manager._claw_loop_node({
            "query": "FLOW 現在多少錢", "history": "", "session_id": "test-session",
            "consent_granted": True,
        })
    assert "0.0271" in result["final_response"]
    assert metrics.call_args.kwargs["route"] == "claw_loop"


def test_claw_loop_node_missing_agent():
    manager = _make_manager()
    manager.agent_registry.get.return_value = None
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "hi", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))
    assert result["final_response"]


# ---------------------------------------------------------------------------
# Error handling — Hermes-style: 429 → short backoff + one retry;
# other errors → friendly message via _friendly_llm_error.
# See docs/CLAW_REFACTOR_PLAN.md Phase 5.
# ---------------------------------------------------------------------------


def test_claw_loop_node_retries_once_on_429(monkeypatch):
    """429 (rate limit) → one short backoff retry; success on 2nd call."""
    import core.agents.manager.claw_loop as claw_loop_mod

    # Avoid actually sleeping in the retry path.
    monkeypatch.setattr(claw_loop_mod.asyncio, "sleep", AsyncMock())

    manager = _make_manager()
    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(
        side_effect=[
            RuntimeError("Error code: 429 - {'status': 429, 'message': 'rate limit'}"),
            AgentResult(
                success=True,
                message="BTC 現價 $95,000",
                agent_name="cryptomind",
                data={"used_tools": ["get_crypto_price"]},
            ),
        ]
    )
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "BTC 多少", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))

    assert fake_agent.execute_streaming.await_count == 2  # retried exactly once
    assert "95,000" in result["final_response"]


def test_claw_loop_node_429_retries_exhausted_returns_friendly(monkeypatch):
    """429 on every attempt → friendly rate-limit message, not raw error text."""
    import core.agents.manager.claw_loop as claw_loop_mod

    monkeypatch.setattr(claw_loop_mod.asyncio, "sleep", AsyncMock())

    manager = _make_manager()
    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(
        side_effect=RuntimeError("Error code: 429 - too many requests")
    )
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "BTC 多少", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))

    # retried once, then gave up
    assert fake_agent.execute_streaming.await_count == 2
    # friendly 429 message, never the raw error string
    assert "429" in result["final_response"]
    assert "Error code" not in result["final_response"]


# ---------------------------------------------------------------------------
# NVIDIA NIM ResourceExhausted — 2026-07-20 修復
#
# 過去 claw_loop 的 retry 邏輯不會被觸發（execute_streaming 在內部吞掉例外），
# 導致 NIM 的 ``ResourceExhausted: Worker local total request limit reached
# (16/16)`` 原文噴進聊天泡泡。Fix 6c 讓 execute_streaming 對 rate-limit 例外
# re-raise，claw_loop 就能接手做 retry-once。
# ---------------------------------------------------------------------------


def test_claw_loop_node_retries_once_on_nim_resource_exhausted(monkeypatch):
    """NIM ResourceExhausted → retry once（slot 通常 1-2s 後釋放）。"""
    import core.agents.manager.claw_loop as claw_loop_mod

    monkeypatch.setattr(claw_loop_mod.asyncio, "sleep", AsyncMock())

    manager = _make_manager()
    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(
        side_effect=[
            RuntimeError(
                "ResourceExhausted: Worker local total request limit reached (16/16)"
            ),
            AgentResult(
                success=True,
                message="BTC 現價 $95,000",
                agent_name="cryptomind",
                data={"used_tools": ["get_crypto_price"]},
            ),
        ]
    )
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "BTC 多少", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))

    assert fake_agent.execute_streaming.await_count == 2
    assert "95,000" in result["final_response"]


def test_claw_loop_node_nim_retries_exhausted_returns_friendly(monkeypatch):
    """NIM ResourceExhausted retries 用完 → NIM 專屬友善訊息，不含原文。"""
    import core.agents.manager.claw_loop as claw_loop_mod

    monkeypatch.setattr(claw_loop_mod.asyncio, "sleep", AsyncMock())

    manager = _make_manager()
    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(
        side_effect=RuntimeError(
            "ResourceExhausted: Worker local total request limit reached (16/16)"
        )
    )
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "BTC 多少", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))

    assert fake_agent.execute_streaming.await_count == 2
    final = result["final_response"]
    # 不可再含原文關鍵字（過去的 bug）
    assert "ResourceExhausted" not in final
    assert "16/16" not in final
    assert "Worker local" not in final
    # 應該含 NIM 專屬訊息關鍵字（友善化後）
    assert "繁忙" in final or "overloaded" in final.lower() or "并发" in final


def test_claw_loop_node_auth_error_is_not_retried(monkeypatch):
    """Non-rate-limit errors (e.g. 401) must NOT retry — fail fast & friendly."""
    import core.agents.manager.claw_loop as claw_loop_mod

    slept = AsyncMock()
    monkeypatch.setattr(claw_loop_mod.asyncio, "sleep", slept)

    manager = _make_manager()
    fake_agent = MagicMock()
    fake_agent.execute_streaming = AsyncMock(
        side_effect=RuntimeError("Error code: 401 - invalid api key")
    )
    manager.agent_registry.get.return_value = fake_agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)

    state = {"query": "BTC 多少", "history": "", "session_id": "test-session"}
    result = asyncio.run(manager._claw_loop_node(state))

    assert fake_agent.execute_streaming.await_count == 1  # no retry
    # heartbeat（每 HEARTBEAT_INTERVAL_SECONDS 秒送一次進度）也會 await
    # asyncio.sleep，所以不能斷言「完全沒 sleep 過」。精確斷言「沒有 retry
    # backoff」——backoff 的秒數不等於 heartbeat 間隔。
    backoff_sleeps = [
        call
        for call in slept.await_args_list
        if call.args and call.args[0] != claw_loop_mod.HEARTBEAT_INTERVAL_SECONDS
    ]
    assert not backoff_sleeps, f"401 不該重試，卻發生 backoff: {backoff_sleeps}"
    assert "API Key" in result["final_response"]  # friendly 401 hint


# ---------------------------------------------------------------------------
# execute_streaming
# ---------------------------------------------------------------------------


def test_execute_streaming_collects_reply_and_tools():
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

    from core.agents.agents.cryptomind_agent import CryptoMindAgent
    from core.agents.models import SubTask

    agent = CryptoMindAgent(llm_client=MagicMock(), tool_registry=None)
    agent._get_tool_metas = MagicMock(return_value=[])

    final_msg = AIMessage(content="FLOW 現價 $0.0271")

    async def fake_astream(_input, stream_mode=None):
        yield (
            "messages",
            (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": "get_crypto_price", "args": "", "id": "1", "index": 0}
                    ],
                ),
                {},
            ),
        )
        yield (
            "messages",
            (
                ToolMessage(
                    content="0.0271", tool_call_id="1", name="get_crypto_price"
                ),
                {},
            ),
        )
        yield ("messages", (AIMessageChunk(content="FLOW 現價 "), {}))
        yield ("messages", (AIMessageChunk(content="$0.0271"), {}))
        yield ("values", {"messages": [final_msg]})

    fake_graph = SimpleNamespace(astream=fake_astream)
    tokens: list = []
    started: list = []
    ended: list = []

    with patch(
        "core.agents.base_react_agent.create_agent", return_value=fake_graph
    ):
        result = asyncio.run(
            agent.execute_streaming(
                SubTask(
                    step=0, description="FLOW 價格", agent="cryptomind", context={}
                ),
                on_token=tokens.append,
                on_tool_start=started.append,
                on_tool_end=ended.append,
            )
        )

    assert result.success is True
    assert result.message == "FLOW 現價 $0.0271"
    assert result.data["used_tools"] == ["get_crypto_price"]
    assert "".join(tokens) == "FLOW 現價 $0.0271"
    assert started == ["get_crypto_price"]
    assert ended == ["get_crypto_price"]
