"""Tests for ManagerState concurrent-update tolerance.

Regression: 多標的查詢（台積+三星+蘋果）或連續對話失敗 resume 時，
LangGraph 同一 step 內多次寫入 session_id/query（無 reducer 的純量 channel）
會拋 INVALID_CONCURRENT_GRAPH_UPDATE: Can receive only one value per step，
導致整個對話崩潰（RuntimeError: generator didn't stop after athrow）。

修補：session_id/query 加 last_value_reducer（右邊覆蓋左邊），並發寫入取最後值。
"""

from typing import get_args, get_type_hints

from core.agents.models import ManagerState, last_value_reducer


def test_last_value_reducer_right_overrides_left():
    """reducer 核心：右邊值覆蓋左邊（last-write-wins）。"""
    assert last_value_reducer("old", "new") == "new"
    assert last_value_reducer(None, "x") == "x"


def test_last_value_reducer_none_keeps_left():
    """右邊 None 時保留左邊（不誤清空）。"""
    assert last_value_reducer("keep", None) == "keep"
    assert last_value_reducer(None, None) is None


def test_session_id_has_reducer():
    """session_id 必須宣告 Annotated + last_value_reducer。

    沒有 reducer 時，連續對話/多標的查詢 resume 會崩潰
    （INVALID_CONCURRENT_GRAPH_UPDATE）。
    """
    hints = get_type_hints(ManagerState, include_extras=True)
    sid_hint = hints.get("session_id")
    assert sid_hint is not None, "session_id 欄位不存在"
    metadata = get_args(sid_hint)
    # Annotated[X, reducer] → get_args 回傳 (X, reducer)
    assert len(metadata) == 2, f"session_id 應為 Annotated[str, reducer]，得 {sid_hint}"
    assert metadata[1] is last_value_reducer, (
        f"session_id 的 reducer 應為 last_value_reducer，得 {metadata[1]}"
    )


def test_query_has_reducer():
    """query 同樣需要 last_value_reducer（連續對話時 input + node 都會寫）。"""
    hints = get_type_hints(ManagerState, include_extras=True)
    q_hint = hints.get("query")
    assert q_hint is not None
    metadata = get_args(q_hint)
    assert len(metadata) == 2, f"query 應為 Annotated[str, reducer]，得 {q_hint}"
    assert metadata[1] is last_value_reducer


def test_concurrent_writes_dont_crash():
    """模擬 LangGraph 並發寫入 session_id：兩個值進 reducer 應回最後值，不拋例外。"""
    # 模擬 graph 在同一 step 收到 input 的 session_id 與 node 寫的 session_id
    val_a = "session-abc"
    val_b = "session-xyz"
    merged = last_value_reducer(val_a, val_b)
    assert merged == "session-xyz", "並發寫入應取最後值，不崩潰"
