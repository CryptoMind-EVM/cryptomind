"""Tests for manager experience recording + hint injection."""

import asyncio
from unittest.mock import MagicMock, patch

from core.agents.manager import ManagerAgent


def test_record_experience_background_called_after_track():
    """_track_conversation fires _record_experience_background as a background task."""
    manager = MagicMock(spec=ManagerAgent)
    manager.user_id = "u1"
    manager.session_id = "s1"
    manager._message_count = 0
    manager._last_consolidated_index = 0
    manager._consolidating = False
    manager._last_activity_time = 0.0

    # _track_conversation calls _get_memory_store().get_last_consolidated_index()
    mock_memory_store = MagicMock()
    mock_memory_store.get_last_consolidated_index.return_value = 0
    manager._get_memory_store.return_value = mock_memory_store

    calls = []

    def fake_run_background(coro):
        calls.append(coro)
        return None

    with patch("core.agents.manager._run_background", side_effect=fake_run_background):
        asyncio.run(
            ManagerAgent._track_conversation(
                manager, "BTC price?", "BTC is 42000", tools_used=["get_crypto_price"]
            )
        )

    assert len(calls) >= 1


def test_experience_hint_injected_in_prompt_when_available():
    """When retrieve_relevant returns results, prompt includes hint block."""
    from core.database.experiences import ExperienceStore

    hint_rows = [
        {
            "task_family": "crypto",
            "query_text": "BTC走勢",
            "tools_used": ["get_crypto_price"],
            "agent_used": "crypto",
            "outcome": "success",
            "quality_score": 1.0,
            "failure_reason": None,
            "created_at": "2026-03-15",
        },
    ]
    store = ExperienceStore()
    hint = store.format_for_prompt(hint_rows)
    assert "BTC走勢" in hint
    assert "get_crypto_price" in hint
    assert "相關過去經驗" in hint


def test_experience_hint_empty_string_when_no_results():
    from core.database.experiences import ExperienceStore

    store = ExperienceStore()
    assert store.format_for_prompt([]) == ""


# ============================================================================
# 方向①: task_family 從 tools_used 推導(修正 task_family 永遠 "chat" 的 bug)
# ============================================================================


def test_infer_task_family_from_tools():
    """工具名前綴應正確推導出市場分類。"""
    from core.agents.manager.memory import _infer_task_family_from_tools

    assert _infer_task_family_from_tools(["get_crypto_price"]) == "crypto"
    assert _infer_task_family_from_tools(["tw_price", "tw_technical"]) == "tw_stock"
    assert _infer_task_family_from_tools(["us_stock_price"]) == "us_stock"
    assert _infer_task_family_from_tools(["get_forex_rate"]) == "forex"
    assert _infer_task_family_from_tools(["get_commodity_price"]) == "commodity"
    # 無匹配 → 空字串(保留 "chat" 預設)
    assert _infer_task_family_from_tools(["web_search"]) == ""
    assert _infer_task_family_from_tools([]) == ""


# ============================================================================
# 方向①: _resolve_experience_hint 注入(讓 agent 記得過去經驗)
# ============================================================================


def test_resolve_experience_hint_returns_none_for_anonymous():
    """訪客(user_id=None)不查經驗,直接回 None。"""
    from core.agents.manager.claw_loop import ClawLoopMixin

    # 用一個簡易 stub 物件模擬 mixin 實例(user_id=None)
    class _Stub(ClawLoopMixin):
        user_id = None
        session_id = "s1"

    stub = _Stub()
    result = asyncio.run(stub._resolve_experience_hint("BTC price?"))
    assert result is None


def test_resolve_experience_hint_returns_formatted_hint():
    """有相關經驗時,回傳 format_for_prompt 的結果。"""
    from core.agents.manager.claw_loop import ClawLoopMixin

    hint_rows = [
        {
            "task_family": "crypto",
            "query_text": "BTC走勢",
            "tools_used": ["get_crypto_price"],
            "agent_used": "crypto",
            "outcome": "success",
            "quality_score": 1.0,
            "failure_reason": None,
            "created_at": "2026-03-15",
        }
    ]

    class _Stub(ClawLoopMixin):
        user_id = "u1"
        session_id = "s1"

    stub = _Stub()

    async def fake_run_sync(fn, *args):
        # 模擬 retrieve_relevant 回傳 hint_rows
        return hint_rows

    with patch("core.agents.manager.claw_loop.run_sync", side_effect=fake_run_sync):
        result = asyncio.run(stub._resolve_experience_hint("BTC price?"))

    assert result is not None
    assert "BTC走勢" in result
    assert "相關過去經驗" in result


def test_resolve_experience_hint_returns_none_when_no_results():
    """無相關經驗時回 None(不注入空區塊)。"""
    from core.agents.manager.claw_loop import ClawLoopMixin

    class _Stub(ClawLoopMixin):
        user_id = "u1"
        session_id = "s1"

    stub = _Stub()

    async def fake_run_sync(fn, *args):
        return []  # 無經驗

    with patch("core.agents.manager.claw_loop.run_sync", side_effect=fake_run_sync):
        result = asyncio.run(stub._resolve_experience_hint("冷門問題"))

    assert result is None


def test_resolve_experience_hint_silent_on_db_error():
    """DB 查詢失敗時靜默回 None,不炸主流程。"""
    from core.agents.manager.claw_loop import ClawLoopMixin

    class _Stub(ClawLoopMixin):
        user_id = "u1"
        session_id = "s1"

    stub = _Stub()

    async def fake_run_sync(fn, *args):
        raise RuntimeError("db down")

    with patch("core.agents.manager.claw_loop.run_sync", side_effect=fake_run_sync):
        result = asyncio.run(stub._resolve_experience_hint("BTC?"))

    assert result is None  # 靜默,不拋


# ============================================================================
# ============================================================================


