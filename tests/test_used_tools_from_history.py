"""used_tools 不該依賴 provider 的串流形狀（2026-09-12 DeepSeek 實測 ToolMessage 沒進 chunk 流）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.agents.verification import merge_used_tools_from_messages

pytestmark = pytest.mark.unit


def test_merges_tool_names_from_final_messages_without_duplicates():
    msgs = [
        HumanMessage(content="BTC?"),
        AIMessage(content=""),
        ToolMessage(content="{}", name="get_crypto_price", tool_call_id="1"),
        ToolMessage(content="{}", name="get_crypto_price", tool_call_id="2"),
        ToolMessage(content="{}", name="get_crypto_technicals", tool_call_id="3"),
    ]
    assert merge_used_tools_from_messages(["get_crypto_price"], msgs) == [
        "get_crypto_price",
        "get_crypto_technicals",
    ]
    assert merge_used_tools_from_messages([], []) == []


def test_execute_streaming_merges_before_building_result():
    src = (
        Path(__file__).resolve().parents[1] / "core/agents/base_react_agent.py"
    ).read_text(encoding="utf-8")
    body = src[src.index("async def execute_streaming") :]
    assert body.index(
        "merge_used_tools_from_messages(used_tools, messages_history)"
    ) < body.index('"used_tools": used_tools')
