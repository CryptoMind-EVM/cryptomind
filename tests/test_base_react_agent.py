from unittest.mock import MagicMock

from core.agents.base_react_agent import BaseReActAgent
from core.agents.models import SubTask
from core.agents.tool_registry import ToolMetadata


class FakePriceTool:
    name = "get_crypto_price"
    description = "獲取加密貨幣的即時價格"
    args = {"symbol": {"type": "string"}}

    def invoke(self, kwargs):
        return {"symbol": kwargs["symbol"], "price": 123.45}


class DummyAgent(BaseReActAgent):
    @property
    def name(self) -> str:
        return "crypto"


class DummyToolRegistry:
    def list_for_agent(self, _agent_name):
        return [
            ToolMetadata(
                name="get_crypto_price",
                description="獲取加密貨幣即時價格",
                input_schema={"symbol": "str"},
                handler=FakePriceTool(),
                allowed_agents=["crypto"],
                role="market_lookup",
                priority=100,
            )
        ]


def test_execute_forces_tool_before_llm_when_tool_required():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="BTC 現價為 123.45 美元。")
    agent = DummyAgent(llm, DummyToolRegistry())

    task = SubTask(
        step=1,
        description="BTC 現在多少錢？",
        agent="crypto",
        context={
            "language": "zh-TW",
            "tool_required": True,
            "symbols": {"crypto": "BTC", "tw": None, "us": None},
        },
    )

    result = agent.execute(task)

    assert result.success is True
    assert "123.45" in result.message
    llm.invoke.assert_called_once()


def test_verified_mode_fails_when_required_tool_is_unavailable():
    llm = MagicMock()

    class EmptyRegistry:
        def list_for_agent(self, _agent_name):
            return []

    agent = DummyAgent(llm, EmptyRegistry())
    task = SubTask(
        step=1,
        description="AAPL 現在多少？",
        agent="crypto",
        context={
            "language": "zh-TW",
            "tool_required": True,
            "symbols": {"us": "AAPL"},
            "allowed_tools": [],
            "metadata": {
                "market_resolution": {
                    "requires_discovery_lookup": False,
                },
                "query_profile": {
                    "query_type": "price_lookup",
                },
            },
        },
    )

    result = agent.execute(task)

    assert result.success is False
    assert result.quality == "fail"
    assert result.quality_fail_reason == "tool_unavailable"
    assert result.data["query_type"] == "price_lookup"
    assert result.data["resolved_market"] == "us"
    assert result.data["policy_path"] == "market_lookup"
    llm.invoke.assert_not_called()


def test_verified_mode_fails_with_discovery_reason_when_market_resolution_is_missing():
    llm = MagicMock()

    class EmptyRegistry:
        def list_for_agent(self, _agent_name):
            return []

    agent = DummyAgent(llm, EmptyRegistry())
    task = SubTask(
        step=1,
        description="tsm 現在多少？",
        agent="crypto",
        context={
            "language": "zh-TW",
            "tool_required": False,
            "allowed_tools": [],
            "metadata": {
                "market_resolution": {
                    "requires_discovery_lookup": True,
                },
                "query_profile": {
                    "query_type": "price_lookup",
                },
            },
        },
    )

    result = agent.execute(task)

    assert result.success is False
    assert result.quality_fail_reason == "discovery_tool_unavailable"
    assert result.data["query_type"] == "price_lookup"
    assert result.data["resolved_market"] is None
    assert result.data["policy_path"] == "discovery_lookup"
    llm.invoke.assert_not_called()


# ============================================================================
# _parse_history_to_messages — 多行 content + 語系 prefix 回歸測試
# 修復背景：R5「them 是什麼？」事件 → assistant 長回應（含表格 / 多行）被舊版
# parser 逐行切割，第二行以後全丟。加上 User: 被誤當 assistant prefix。
# ============================================================================


def _parse(history_text):
    return BaseReActAgent._parse_history_to_messages(history_text)


def test_parse_history_empty_returns_empty_list():
    assert _parse("") == []
    assert _parse("   \n  \n") == []


def test_parse_history_single_turn_user():
    msgs = _parse("用戶: 比特幣是什麼？")
    assert len(msgs) == 1
    assert msgs[0].__class__.__name__ == "HumanMessage"
    assert msgs[0].content == "比特幣是什麼？"


def test_parse_history_single_turn_assistant():
    msgs = _parse("助手: BTC 是數位黃金")
    assert len(msgs) == 1
    assert msgs[0].__class__.__name__ == "AIMessage"
    assert msgs[0].content == "BTC 是數位黃金"


def test_parse_history_multiline_assistant_content_preserved():
    """修復核心 case：assistant 回應多行（markdown 表格），所有行都要保留。"""
    history = (
        "用戶: 比較 BTC 跟 ETH\n"
        "助手: | 幣別 | 波動率 |\n"
        "|------|--------|\n"
        "| BTC  | 50%    |\n"
        "| ETH  | 65%    |\n"
        "\n"
        "結論：ETH 較波動。"
    )
    msgs = _parse(history)
    assert len(msgs) == 2
    # User message 正確
    assert msgs[0].__class__.__name__ == "HumanMessage"
    assert msgs[0].content == "比較 BTC 跟 ETH"
    # AI message 必須保留多行
    assert msgs[1].__class__.__name__ == "AIMessage"
    assert "| 幣別 | 波動率 |" in msgs[1].content
    assert "| ETH  | 65%    |" in msgs[1].content
    assert "結論：ETH 較波動。" in msgs[1].content


def test_parse_history_simplified_chinese_user_prefix():
    """簡中『用户:』也要能識別為 user。"""
    msgs = _parse("用户: 现在值得买吗？")
    assert len(msgs) == 1
    assert msgs[0].__class__.__name__ == "HumanMessage"
    assert msgs[0].content == "现在值得买吗？"


def test_parse_history_english_assistant_prefix_not_mistaken_as_user():
    """回歸：舊版把英文 "User:" 誤當成 assistant prefix（應為 Assistant:）。"""
    msgs = _parse("User: hello\nAssistant: hi there")
    assert len(msgs) == 2
    assert msgs[0].__class__.__name__ == "HumanMessage"
    assert msgs[0].content == "hello"
    assert msgs[1].__class__.__name__ == "AIMessage"
    assert msgs[1].content == "hi there"


def test_parse_history_multiple_turns_preserve_order_and_multiline():
    history = (
        "用戶: Q1\n"
        "助手: A1 line1\n"
        "A1 line2\n"
        "用戶: Q2\n"
        "助手: A2"
    )
    msgs = _parse(history)
    assert len(msgs) == 4
    assert [m.__class__.__name__ for m in msgs] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
        "AIMessage",
    ]
    assert msgs[1].content == "A1 line1\nA1 line2"
    assert msgs[3].content == "A2"


def test_parse_history_garbage_lines_at_start_ignored():
    """history 開頭若不是 role 開頭的雜訊，應被忽略不爆。"""
    msgs = _parse("(session restored)\n用戶: hi\n助手: hello")
    assert len(msgs) == 2
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"


def test_parse_history_trailing_garbage_attached_to_last_block():
    """尾部雜訊應歸給最後一個 block（合理：可能是被截斷的內容）。"""
    msgs = _parse("用戶: hi\n助手: hello\n殘留訊息")
    assert len(msgs) == 2
    assert msgs[1].content == "hello\n殘留訊息"
