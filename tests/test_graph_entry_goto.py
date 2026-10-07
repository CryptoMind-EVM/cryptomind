"""防回歸測試：確保入口的 Command(goto=...) 指向 graph 實際存在的節點。

背景：CLAW 重構刪除 understand_intent 節點後，漏改 analysis.py / telegram_chat.py
的 Command(goto="understand_intent")，導致 LangGraph 忽略 goto + 從 checkpointer
恢復舊狀態 → 連續對話第二題回吐第一題的答案（狀態洩漏）。

這個測試確保：
1. 入口程式碼裡的 goto 目標，必須是 ManagerAgent graph 實際包含的節點
2. 連續兩個不同請求不會回傳相同結果（狀態洩漏偵測）
"""

import ast
import os
from unittest.mock import AsyncMock, MagicMock

import pytest


def _extract_goto_targets(filepath: str) -> list[str]:
    """從 .py 檔案解析所有 Command(goto="...") 的目標節點名稱。"""
    with open(filepath, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # 找 Command(goto="xxx") 或 kw=Command(goto="xxx")
            func = node.func
            is_command = (
                (isinstance(func, ast.Name) and func.id == "Command")
                or (isinstance(func, ast.Attribute) and func.attr == "Command")
            )
            if is_command:
                for kw in node.keywords:
                    if kw.arg == "goto" and isinstance(kw.value, ast.Constant):
                        targets.append(kw.value.value)
    return targets


def test_analysis_goto_targets_exist_in_graph():
    """analysis.py 的所有 Command(goto=...) 必須指向 graph 實際節點。"""
    from core.agents.manager._main import ManagerAgent

    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="test",
        session_id="test",
    )
    graph_nodes = set(manager.graph.get_graph().nodes.keys())

    filepath = os.path.join(
        os.path.dirname(__file__), "..", "api", "routers", "analysis.py"
    )
    targets = _extract_goto_targets(os.path.abspath(filepath))
    assert targets, "找不到任何 Command(goto=...) — 測試可能失效"

    for target in targets:
        assert target in graph_nodes, (
            f"analysis.py 的 Command(goto={target!r}) 指向不存在的節點。"
            f"Graph 實際節點：{graph_nodes}。"
            f"這會導致 LangGraph 忽略 goto 並從 checkpointer 恢復舊狀態"
            f"（連續對話狀態洩漏 bug）。"
        )


def test_telegram_goto_targets_exist_in_graph():
    """telegram_chat.py 的所有 Command(goto=...) 必須指向 graph 實際節點。"""
    from core.agents.manager._main import ManagerAgent

    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="test",
        session_id="test",
    )
    graph_nodes = set(manager.graph.get_graph().nodes.keys())

    filepath = os.path.join(
        os.path.dirname(__file__), "..", "api", "routers", "telegram_chat.py"
    )
    targets = _extract_goto_targets(os.path.abspath(filepath))
    assert targets, "找不到任何 Command(goto=...) — 測試可能失效"

    for target in targets:
        assert target in graph_nodes, (
            f"telegram_chat.py 的 Command(goto={target!r}) 指向不存在的節點。"
            f"Graph 實際節點：{graph_nodes}。"
        )


@pytest.mark.asyncio
async def test_consecutive_queries_do_not_leak_state(monkeypatch):
    """連續兩個不同查詢不應回傳相同結果（狀態洩漏偵測）。

    這正是 production bug 的核心症狀：第二題回吐第一題的答案。
    用 mock LLM 隔離 graph 路由行為，不依賴真實 LLM。
    """
    import asyncio

    from langgraph.types import Command

    from core.agents.manager import MANAGER_GRAPH_RECURSION_LIMIT
    from core.agents.manager._main import ManagerAgent
    from core.agents.models import AgentResult

    # Mock cryptomind agent：每次回應 echo 它收到的 query，讓我們能區分兩輪
    received_queries = []

    async def fake_execute_streaming(task, **kwargs):
        received_queries.append(task.description)
        return AgentResult(
            success=True,
            message=f"回應：{task.description[:40]}",
            agent_name="cryptomind",
            data={"used_tools": []},
        )

    fake_agent = MagicMock()
    fake_agent.execute_streaming = fake_execute_streaming

    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="leak-test",
        session_id="leak-test",
    )
    manager.agent_registry.get.return_value = fake_agent
    manager.check_idle_consolidation = MagicMock(return_value=False)
    manager._track_conversation = AsyncMock()

    config = {
        "configurable": {"thread_id": "leak-test"},
        "recursion_limit": MANAGER_GRAPH_RECURSION_LIMIT,
    }

    # 模擬 analysis.py 的入口：Command(goto=ENTRY_NODE, update={query: ...})
    entry_node = "claw_loop"  # 必須與 analysis.py 一致

    # 請求 1
    cmd1 = Command(
        goto=entry_node,
        update={"query": "現在幾點", "session_id": "leak-test",
                "history": "", "language": "zh-TW"},
    )
    await asyncio.wait_for(manager.graph.ainvoke(cmd1, config), timeout=60)

    # 請求 2（不同查詢）
    cmd2 = Command(
        goto=entry_node,
        update={"query": "BTC 多少錢", "session_id": "leak-test",
                "history": "", "language": "zh-TW"},
    )
    await asyncio.wait_for(manager.graph.ainvoke(cmd2, config), timeout=60)

    # 核心斷言：兩個不同查詢，agent 必須收到不同的 query。
    # 比較實際傳入 agent 的 task.description（會含時間錨點前綴 + query），
    # 而非 final_response（mock echo 時可能因截斷碰巧相同）。
    assert len(received_queries) >= 2, (
        f"agent 應被呼叫兩次，實際 {len(received_queries)} 次"
    )
    # 第二次收到的 query 必須包含「BTC」，不能仍是「現在幾點」
    assert "BTC" in received_queries[1], (
        f"狀態洩漏！第二個請求「BTC 多少錢」沒傳到 agent。\n"
        f"agent 第1次收到: {received_queries[0]!r}\n"
        f"agent 第2次收到: {received_queries[1]!r}\n"
        f"第二個應包含 'BTC'，這代表 Command(goto=不存在的節點) 被忽略，"
        f"query 未更新，從 checkpointer 恢復舊狀態。"
    )
    assert "現在幾點" in received_queries[0], (
        f"第一個請求沒正確傳到 agent: {received_queries[0]!r}"
    )
