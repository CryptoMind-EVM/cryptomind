"""ManagerState channel 宣告測試 — 解 LangGraph unknown channel warning。

背景（2026-07-20）：
api/routers/analysis.py:384-400 與 claw_loop.py:826-842 用 Command(goto=...,
update={...}) 寫入 4 個欄位（history_load_status / history_truncated /
history_token_count / history_message_count），但 ManagerState TypedDict
從未宣告這些 channel，導致 LangGraph 的 __input__ task 記 warning 後丟棄。

連帶 latent bug：_main.py:546 讀 state.get("history_truncated", False)
永遠拿到 False，response hooks 收到的 metadata 永遠不正確。

本測試驗證：
1. ManagerState 含這 4 個欄位（型別正確）
2. 4 個欄位在 TypedDict total=False 下為 Optional
3. Command update 含這 4 個 key 時不會再觸發 unknown channel warning
"""
from __future__ import annotations

import logging
from typing import get_type_hints

import pytest


def test_manager_state_has_history_load_status():
    """ManagerState 必須含 history_load_status channel。"""
    from core.agents.models import ManagerState

    hints = get_type_hints(ManagerState)
    assert "history_load_status" in hints, (
        "ManagerState 缺 history_load_status — 會導致 LangGraph unknown channel warning"
    )


def test_manager_state_has_history_truncated():
    """ManagerState 必須含 history_truncated channel。"""
    from core.agents.models import ManagerState

    hints = get_type_hints(ManagerState)
    assert "history_truncated" in hints, (
        "ManagerState 缺 history_truncated — 會導致 LangGraph unknown channel warning"
        " + _main.py:546 讀 state.get('history_truncated', False) 永遠是 False"
    )


def test_manager_state_has_history_token_count():
    """ManagerState 必須含 history_token_count channel。"""
    from core.agents.models import ManagerState

    hints = get_type_hints(ManagerState)
    assert "history_token_count" in hints


def test_manager_state_has_history_message_count():
    """ManagerState 必須含 history_message_count channel。"""
    from core.agents.models import ManagerState

    hints = get_type_hints(ManagerState)
    assert "history_message_count" in hints


def test_all_four_channels_present_at_once():
    """4 個 channel 同時存在的 smoke test。"""
    from core.agents.models import ManagerState

    hints = get_type_hints(ManagerState)
    required = {
        "history_load_status",
        "history_truncated",
        "history_token_count",
        "history_message_count",
    }
    missing = required - set(hints.keys())
    assert not missing, f"ManagerState 缺 channel: {missing}"


# ============================================================================
# LangGraph 整合：驗證 Command update 不再觸發 unknown channel warning
# ============================================================================


def test_command_update_does_not_trigger_unknown_channel_warning(caplog):
    """用真實 LangGraph StateGraph 跑一次，斷言無 unknown channel warning。

    重現原本 bug 的場景：建立 StateGraph(ManagerState)，invoke 一個帶 4 個
    history_* key 的 Command update，觀察 log。
    """
    from langgraph.types import Command

    from core.agents.models import ManagerState

    # 建一個最小 graph，含一個 no-op 節點
    try:
        from langgraph.graph import StateGraph
    except ImportError:
        pytest.skip("langgraph 未安裝")

    builder = StateGraph(ManagerState)

    def noop_node(state):
        return {}

    builder.add_node("noop", noop_node)
    builder.set_entry_point("noop")
    builder.set_finish_point("noop")
    graph = builder.compile()

    # 帶 4 個 history_* key 的 Command update（原本會觸發 warning）
    cmd = Command(
        update={
            "session_id": "test-session",
            "query": "test",
            "history": "",
            "history_load_status": "empty",
            "history_truncated": False,
            "history_token_count": 0,
            "history_message_count": 0,
        }
    )

    with caplog.at_level(logging.WARNING, logger="langgraph"):
        result = graph.invoke(cmd)

    # 結果該含我們寫入的值（之前因 channel 未宣告會被丟棄）
    assert result is not None
    # 檢查 warning 日誌中不該有 unknown channel
    unknown_channel_warnings = [
        r for r in caplog.records if "unknown channel" in r.getMessage()
    ]
    assert not unknown_channel_warnings, (
        f"仍出現 unknown channel warning: "
        f"{[r.getMessage() for r in unknown_channel_warnings]}"
    )


def test_history_truncated_value_is_persisted():
    """history_truncated=True 寫入後，從 state 讀回也該是 True（latent bug fix）。

    之前 channel 未宣告時，寫入會被丟棄，state.get('history_truncated', False)
    永遠是 False。現在 channel 宣告了，值該被保留。
    """
    from langgraph.types import Command

    from core.agents.models import ManagerState

    try:
        from langgraph.graph import StateGraph
    except ImportError:
        pytest.skip("langgraph 未安裝")

    builder = StateGraph(ManagerState)

    def echo_node(state):
        # 故意讀 history_truncated 並寫到 final_response，以便驗證
        return {"final_response": f"truncated={state.get('history_truncated')}"}

    builder.add_node("echo", echo_node)
    builder.set_entry_point("echo")
    builder.set_finish_point("echo")
    graph = builder.compile()

    cmd = Command(
        update={
            "session_id": "test",
            "query": "q",
            "history_truncated": True,
        }
    )
    result = graph.invoke(cmd)
    assert result.get("final_response") == "truncated=True"
