from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import StructuredTool


def _make_tool(return_value, name="mock_tool"):
    tool = MagicMock()
    tool.name = name
    tool.invoke = MagicMock(return_value=return_value)
    return tool


@pytest.fixture(autouse=True)
def reset_compactor_state():
    from core.agents import tool_compactor

    tool_compactor._reset_for_testing()
    yield
    tool_compactor._reset_for_testing()


def test_small_output_passes_through():
    from core.agents.tool_compactor import wrap_tool

    tool = _make_tool({"price": 100})
    wrapped = wrap_tool(tool)
    assert wrapped.invoke({}) == {"price": 100}


def test_large_string_output_is_compacted():
    from core.agents.tool_compactor import THRESHOLD, wrap_tool

    tool = _make_tool("x" * (THRESHOLD + 1))
    with patch("core.agents.tool_compactor._store_sync", return_value="test-uuid-123"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    # 壓縮後 LLM 看到截斷提示，但 UUID 不該暴露（避免 LLM 把它吐給用戶）
    assert "test-uuid-123" not in result, "UUID 不該出現在壓縮結果裡（會被 LLM 轉發）"
    assert "完整資料已截斷" in result


def test_large_dict_output_is_compacted():
    from core.agents.tool_compactor import wrap_tool

    tool = _make_tool({"rows": ["row"] * 1000})
    with patch("core.agents.tool_compactor._store_sync", return_value="uuid-abc"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    assert "uuid-abc" not in result, "UUID 不該出現在壓縮結果裡"
    assert "完整資料已截斷" in result


def test_compact_summary_contains_preview():
    from core.agents.tool_compactor import THRESHOLD, wrap_tool

    original = "PREVIEW_MARKER" + "A" * (THRESHOLD + 500)
    tool = _make_tool(original)
    with patch("core.agents.tool_compactor._store_sync", return_value="uid"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    assert "PREVIEW_MARKER" in result


def test_tool_name_preserved():
    from core.agents.tool_compactor import wrap_tool

    tool = _make_tool("small output", name="tw_stock_price")
    wrapped = wrap_tool(tool)
    assert wrapped.name == "tw_stock_price"


def test_wrap_tool_does_not_mutate_original():
    from core.agents.tool_compactor import THRESHOLD, wrap_tool

    tool = _make_tool("x" * (THRESHOLD + 100))
    original_invoke = tool.invoke
    with patch("core.agents.tool_compactor._store_sync", return_value="uid"):
        wrapped = wrap_tool(tool)

    assert tool.invoke is original_invoke
    assert wrapped is not tool


def test_multiple_wraps_do_not_stack():
    from core.agents.tool_compactor import THRESHOLD, wrap_tool

    tool = _make_tool("x" * (THRESHOLD + 100))
    store_calls = []

    def fake_store(data, owner_id=None, workspace_id=None, session_id=None):
        store_calls.append(data)
        return f"uid-{len(store_calls)}"

    with patch("core.agents.tool_compactor._store_sync", side_effect=fake_store):
        wrapped1 = wrap_tool(tool)
        wrapped2 = wrap_tool(tool)
        wrapped1.invoke({})
        wrapped2.invoke({})

    assert len(store_calls) == 2


def test_store_sync_returns_uuid_string():
    from core.agents.tool_compactor import _store_sync

    with patch("core.agents.tool_compactor._get_redis_sync", return_value=None):
        uid = _store_sync("some data")

    assert isinstance(uid, str) and len(uid) > 0


def test_retrieve_sync_returns_none_for_unknown_key():
    from core.agents.tool_compactor import _retrieve_sync

    with patch("core.agents.tool_compactor._get_redis_sync", return_value=None):
        assert _retrieve_sync("nonexistent-uuid") is None


def test_retrieve_tool_result_error_message():
    from core.agents.tool_compactor import retrieve_tool_result

    with patch("core.agents.tool_compactor._get_redis_sync", return_value=None):
        assert "[ERROR]" in retrieve_tool_result("bad-key")


def test_retrieve_tool_result_returns_stored_data():
    from core.agents.tool_compactor import retrieve_tool_result

    with patch(
        "core.agents.tool_compactor._local_store", {"my-uid": "full content here"}
    ):
        with patch("core.agents.tool_compactor._get_redis_sync", return_value=None):
            assert retrieve_tool_result("my-uid") == "full content here"


def test_retrieve_tool_result_rejects_other_user():
    from core.agents.tool_compactor import _serialize_record, retrieve_tool_result

    with patch(
        "core.agents.tool_compactor._local_store",
        {"owned-uid": _serialize_record("secret", "user-a")},
    ):
        with patch("core.agents.tool_compactor._get_redis_sync", return_value=None):
            result = retrieve_tool_result("owned-uid", requester_id="user-b")

    assert "[ERROR]" in result


def test_retrieve_tool_result_rejects_other_workspace():
    from core.agents.tool_compactor import _serialize_record, retrieve_tool_result

    with patch(
        "core.agents.tool_compactor._local_store",
        {"owned-uid": _serialize_record("secret", "user-a", workspace_id="ws-a")},
    ):
        with patch("core.agents.tool_compactor._get_redis_sync", return_value=None):
            result = retrieve_tool_result(
                "owned-uid", requester_id="user-a", workspace_id="ws-b"
            )

    assert "[ERROR]" in result


def test_base_react_agent_wraps_tools_non_mutating():
    from core.agents.base_react_agent import BaseReActAgent
    from core.agents.tool_compactor import _CompactingToolWrapper
    from core.agents.tool_registry import ToolMetadata

    class DummyAgent(BaseReActAgent):
        @property
        def name(self) -> str:
            return "dummy"

    big_tool = _make_tool("x" * 5000, name="big_tool")
    original_invoke = big_tool.invoke
    registry_meta = ToolMetadata(
        name="big_tool",
        description="test",
        input_schema={},
        handler=big_tool,
        allowed_agents=[],
        required_tier="free",
    )
    registry = MagicMock()
    registry.list_for_agent.return_value = [registry_meta]
    agent = DummyAgent(llm_client=MagicMock(), tool_registry=registry, user_id="user-1")

    with patch(
        "core.agents.base_react_agent.get_allowed_tools", return_value=["big_tool"]
    ):
        metas = agent._get_tool_metas()

    assert isinstance(metas[0].handler, _CompactingToolWrapper)
    assert metas[0].handler._owner_id == "user-1"
    assert big_tool.invoke is original_invoke
    assert registry_meta.handler is big_tool


def test_base_react_agent_wraps_tools_with_workspace_scope():
    from core.agents.base_react_agent import BaseReActAgent
    from core.agents.tool_registry import ToolMetadata

    class DummyAgent(BaseReActAgent):
        @property
        def name(self) -> str:
            return "dummy"

    tool = _make_tool("small")
    registry_meta = ToolMetadata(
        name="tool",
        description="test",
        input_schema={},
        handler=tool,
        allowed_agents=[],
    )
    registry = MagicMock()
    registry.list_for_agent.return_value = [registry_meta]
    agent = DummyAgent(llm_client=MagicMock(), tool_registry=registry, user_id="user-1")

    task = MagicMock()
    task.context = {"workspace_id": "ws-1", "user_id": "user-1"}

    with patch("core.agents.base_react_agent.get_allowed_tools", return_value=["tool"]):
        metas = agent._get_tool_metas(task)

    assert metas[0].handler._workspace_id == "ws-1"


def test_repeated_get_tool_metas_does_not_double_wrap():
    from core.agents.base_react_agent import BaseReActAgent
    from core.agents.tool_registry import ToolMetadata

    class DummyAgent(BaseReActAgent):
        @property
        def name(self) -> str:
            return "dummy"

    tool = _make_tool("small output")
    registry_meta = ToolMetadata(
        name="t",
        description="",
        input_schema={},
        handler=tool,
        allowed_agents=[],
    )
    registry = MagicMock()
    registry.list_for_agent.return_value = [registry_meta]
    agent = DummyAgent(llm_client=MagicMock(), tool_registry=registry)

    with patch("core.agents.base_react_agent.get_allowed_tools", return_value=["t"]):
        metas1 = agent._get_tool_metas()
        metas2 = agent._get_tool_metas()

    assert metas1[0].handler._original is tool
    assert metas2[0].handler._original is tool


def test_required_tool_path_compacts_large_output():
    from core.agents.base_react_agent import BaseReActAgent
    from core.agents.models import AgentResult, SubTask
    from core.agents.tool_registry import ToolMetadata

    class DummyAgent(BaseReActAgent):
        @property
        def name(self) -> str:
            return "dummy"

    big_tool = _make_tool("x" * 5000, name="forced_tool")
    registry_meta = ToolMetadata(
        name="forced_tool",
        description="test",
        input_schema={"symbol": "str"},
        handler=big_tool,
        allowed_agents=[],
        required_tier="free",
        role="market_lookup",
    )
    registry = MagicMock()
    registry.list_for_agent.return_value = [registry_meta]
    agent = DummyAgent(llm_client=MagicMock(), tool_registry=registry)
    captured = {}

    def fake_summarize(self, task, meta, tool_result, language):
        captured["tool_result"] = tool_result
        return AgentResult(success=True, message="ok", agent_name="dummy")

    task = SubTask(
        step=1,
        description="test",
        agent="dummy",
        context={"symbols": {"crypto": "BTC"}, "tool_required": True},
    )

    with patch("core.agents.tool_compactor._store_sync", return_value="forced-uid"):
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["forced_tool"],
        ):
            with patch.object(
                type(agent), "_summarize_required_tool_result", fake_summarize
            ):
                wrapped_metas = agent._get_tool_metas(task)
                agent._execute_with_required_tool(task, wrapped_metas, "zh-TW")

    tool_result_str = str(captured.get("tool_result", ""))
    assert "forced-uid" not in tool_result_str, "UUID 不該暴露給 LLM"
    assert "完整資料已截斷" in tool_result_str


# ── tool stat collection ──────────────────────────────────────────────────────


def test_wrapper_records_pending_stat_on_success():
    """After invoke(), wrapper exposes a pending stat tuple."""
    from core.agents.tool_compactor import _reset_for_testing, wrap_tool

    _reset_for_testing()
    tool = _make_tool("small output")
    wrapped = wrap_tool(tool, owner_id="u1")
    wrapped.invoke({})
    assert wrapped.last_stat is not None
    stat = wrapped.last_stat
    assert stat["tool_name"] == "mock_tool"
    assert stat["success"] is True
    assert stat["output_chars"] > 0


def test_wrapper_records_pending_stat_on_failure():
    """After a failing invoke(), last_stat has success=False and error_type set."""
    from core.agents.tool_compactor import _reset_for_testing, wrap_tool

    _reset_for_testing()
    tool = MagicMock()
    tool.name = "fail_tool"
    tool.invoke = MagicMock(side_effect=RuntimeError("API down"))
    wrapped = wrap_tool(tool, owner_id="u1")
    try:
        wrapped.invoke({})
    except RuntimeError:
        pass
    assert wrapped.last_stat is not None
    assert wrapped.last_stat["success"] is False
    assert wrapped.last_stat["error_type"] == "RuntimeError"


def test_structured_tool_wrapper_exposes_last_stat_without_pydantic_field_error():
    """StructuredTool wrapping should work without Pydantic field errors."""
    from core.agents.tool_compactor import wrap_tool

    def small_tool(symbol: str) -> dict:
        """A small tool that returns the symbol."""
        return {"symbol": symbol}

    tool = StructuredTool.from_function(small_tool, name="small_tool")
    wrapped = wrap_tool(tool, owner_id="u1")

    result = wrapped.invoke({"symbol": "BTC"})

    assert result == {"symbol": "BTC"}
    assert wrapped.last_stat is not None
    assert wrapped.last_stat["tool_name"] == "small_tool"
    assert wrapped.last_stat["success"] is True


# ---------------------------------------------------------------------------
# 結構感知截斷：list 型工具輸出（如新聞）不應在第 2 項中間被切斷
# ---------------------------------------------------------------------------


def _make_news_list(n: int = 5):
    """模擬 google_news 的回傳：n 則新聞 dict（含真實的長 Google News URL）。"""
    # 真實 Google News URL 動輒 200+ 字元，5 則序列化後約 2800 字元（>THRESHOLD）
    long_url = (
        "https://news.google.com/rss/articles/"
        "CBMi4wFBVV95cUxPU1NLNHNEcl9oMjBGUjFCSDRhWURCZEF0cGNBUV"
        "9hYmNkZWZnaGlqa2xtbnBxcnN0dXYwMTIzNDU2Nzg5YWJjZGVmZ2hp"
        "amtsbW5vcHFyc3R1dnd4eXoxMjM0NTY3ODkw"
    )
    return [
        {
            "title": f"Bitcoin News Headline Number {i} - Source Name",
            "description": f"Bitcoin News Headline Number {i}&nbsp;&nbsp;Source Name",
            "published_at": f"Mon, {i:02d} Jul 2026 16:54:14 GMT",
            "sentiment": "中性",
            "source": f"Google (Source {i})",
            "url": long_url + str(i),
        }
        for i in range(n)
    ]


def test_list_output_compaction_preserves_multiple_items():
    """list 截斷後預覽應保留 ≥2 則完整項目，而非只留 1 則 + 半個壞 JSON。"""
    from core.agents.tool_compactor import THRESHOLD, wrap_tool

    # 10 則確保遠超過 THRESHOLD，截斷後 budget 仍能容納多則
    news = _make_news_list(10)
    import orjson

    assert len(orjson.dumps(news).decode()) > THRESHOLD

    tool = _make_tool(news, name="google_news")
    with patch("core.agents.tool_compactor._store_sync", return_value="uid"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    result_str = result if isinstance(result, str) else str(result)
    # 應看到至少 2 則的 title（舊版只會有 1 則）
    title_count = result_str.count("Bitcoin News Headline")
    assert title_count >= 2, f"截斷後應保留 ≥2 則，實際 {title_count} 則:\n{result_str}"


def test_list_output_compaction_produces_valid_json():
    """list 截斷後的 JSON 預覽段應可被 json.loads parse（不在項目中間斷裂）。"""
    from core.agents.tool_compactor import wrap_tool

    news = _make_news_list(10)
    tool = _make_tool(news, name="google_news")
    with patch("core.agents.tool_compactor._store_sync", return_value="uid"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    result_str = result if isinstance(result, str) else str(result)
    # 核心：預覽不應含未閉合的 JSON 物件（舊版會在第 2 項中間斷）。
    # 計算花括號深度：若 ≠ 0 表示有物件在序列化途中被切斷。
    brace_depth = 0
    for ch in result_str:
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
    # brace_depth == 0 表示所有物件都閉合（合法）
    assert brace_depth == 0, (
        f"截斷後有 {brace_depth} 個未閉合的 JSON 物件（舊 bug 重現）:\n{result_str[:300]}"
    )


def test_large_string_output_still_compacted_to_preview():
    """純字串大輸出仍走字元截斷（smart_preview 對非 JSON 字串退回原行為）。"""
    from core.agents.tool_compactor import THRESHOLD, wrap_tool

    original = "x" * (THRESHOLD + 1)
    tool = _make_tool(original, name="plain_tool")
    with patch("core.agents.tool_compactor._store_sync", return_value="uid"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    assert "完整資料已截斷" in result
    assert "uid" not in result


def test_list_compaction_shows_truncation_note():
    """list 截斷應附上截斷提示（讓 LLM 知道資料被精簡）。"""
    from core.agents.tool_compactor import wrap_tool

    news = _make_news_list(10)
    tool = _make_tool(news, name="google_news")
    with patch("core.agents.tool_compactor._store_sync", return_value="uid"):
        wrapped = wrap_tool(tool)
        result = wrapped.invoke({})

    result_str = result if isinstance(result, str) else str(result)
    assert "截斷" in result_str or "已顯示" in result_str


# ---------------------------------------------------------------------------
# args_schema 繼承：wrapper 必須暴露原始工具的參數 schema（防 LLM 死循環）
# ---------------------------------------------------------------------------


def test_wrapper_preserves_args_schema():
    """wrapper 必須繼承原始工具的 args_schema，否則 LLM 看到的是
    args(array)+kwargs 而非正確參數名（如 ticker），導致工具永遠叫不到。

    回歸測試：tw_news_tool 的 ticker 參數被 compactor 包裝後必須仍是 string。
    """
    from langchain_core.tools import StructuredTool

    from core.agents.tool_compactor import wrap_tool

    def sample_tool(ticker: str, limit: int = 5) -> dict:
        """A sample tool with a ticker parameter."""
        return {"ticker": ticker}

    tool = StructuredTool.from_function(sample_tool, name="sample_tool")
    wrapped = wrap_tool(tool)

    # wrapper 的 input_schema 必須暴露 ticker（string），不是 args（array）
    schema = wrapped.input_schema.model_json_schema()
    props = schema.get("properties", {})
    assert "ticker" in props, (
        f"wrapper 丟失了 args_schema，LLM 看不到 ticker。properties: {list(props.keys())}"
    )
    assert props["ticker"]["type"] == "string"


# ============================================================================
# Phase 0: per-tool-call consent guard（_audit_if_high_risk 升級）
#
# defense-in-depth：high-risk tool 執行前攔截，補 consent gate 看不到 sub-agent
# 決定的漏洞。預設關閉（CONSENT_TOOL_GUARD_ENABLED），啟用子 agent 前才開。
# ============================================================================


def _make_wrapper_for_audit(risk_level="low", tool_name="mock_tool", owner_id="user1"):
    """組一個 fake wrapper 給 _audit_if_high_risk 測試用。"""
    from unittest.mock import MagicMock

    wrapper = MagicMock()
    wrapper._risk_level = risk_level
    wrapper._owner_id = owner_id
    orig = MagicMock()
    orig.name = tool_name
    wrapper._original = orig
    return wrapper


def test_audit_low_risk_noop():
    """low-risk tool 不觸發任何 guard（直接 return）。"""
    import os

    os.environ.pop("CONSENT_TOOL_GUARD_ENABLED", None)
    from core.agents.tool_compactor import _audit_if_high_risk

    wrapper = _make_wrapper_for_audit(risk_level="low")
    _audit_if_high_risk(wrapper)  # 不該 crash、不該 interrupt


def test_audit_high_risk_guard_disabled_only_audits():
    """high-risk tool + guard 關閉 → 只稽核（不 interrupt、不 crash）。"""
    import os

    os.environ.pop("CONSENT_TOOL_GUARD_ENABLED", None)
    from core.agents.tool_compactor import _audit_if_high_risk

    wrapper = _make_wrapper_for_audit(
        risk_level="high", tool_name="submit_kyc_application"
    )
    _audit_if_high_risk(wrapper)  # graceful,只稽核


def test_audit_high_risk_guard_enabled_triggers_interrupt(monkeypatch):
    """high-risk tool + guard 啟用 → 呼叫 interrupt()（GraphInterrupt 上拋）。"""
    monkeypatch.setenv("CONSENT_TOOL_GUARD_ENABLED", "true")
    from langgraph.errors import GraphInterrupt

    interrupt_calls = []

    def fake_interrupt(payload):
        interrupt_calls.append(payload)
        raise GraphInterrupt()  # 模擬首次暫停

    monkeypatch.setattr("langgraph.types.interrupt", fake_interrupt)
    from core.agents.tool_compactor import _audit_if_high_risk

    wrapper = _make_wrapper_for_audit(
        risk_level="high", tool_name="submit_kyc_application"
    )
    # GraphInterrupt 必須原樣上拋（不能被 graceful 吞掉）
    with pytest.raises(GraphInterrupt):
        _audit_if_high_risk(wrapper)
    assert len(interrupt_calls) == 1
    # type 必須是前端 hitl_question 分派認得的 consent_gate（舊值 "consent"
    # 會掉進 clarify fallback，顯示錯誤的釐清問題——2026-09-21 修正）
    assert interrupt_calls[0]["type"] == "consent_gate"
    assert interrupt_calls[0]["tool_name"] == "submit_kyc_application"


def test_audit_high_risk_guard_approved_does_not_block(monkeypatch):
    """high-risk tool + guard 啟用 + 使用者同意 → 不拋 ToolException（正常繼續）。"""
    monkeypatch.setenv("CONSENT_TOOL_GUARD_ENABLED", "true")

    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "yes")
    from core.agents.tool_compactor import _audit_if_high_risk

    wrapper = _make_wrapper_for_audit(
        risk_level="high", tool_name="get_eth_balance"
    )
    _audit_if_high_risk(wrapper)  # 同意 → 不該拋例外


def test_audit_high_risk_guard_rejected_raises_tool_exception(monkeypatch):
    """high-risk tool + guard 啟用 + 使用者拒絕 → 拋 ToolException（中止工具）。

    ToolException 必須原樣上拋（不能被 graceful 吞掉），否則拒絕無效、工具靜默執行。
    """
    monkeypatch.setenv("CONSENT_TOOL_GUARD_ENABLED", "true")
    from langchain_core.tools import ToolException

    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "no")
    from core.agents.tool_compactor import _audit_if_high_risk

    wrapper = _make_wrapper_for_audit(
        risk_level="high", tool_name="get_address_transactions"
    )
    with pytest.raises(ToolException):
        _audit_if_high_risk(wrapper)


def test_audit_always_logs_regardless_of_guard(monkeypatch):
    """不管 guard 啟用與否，high-risk 都要記稽核 log（defense-in-depth）。"""
    log_calls = []

    monkeypatch.setattr(
        "core.agents.manager.consent_gate.log_high_risk_tool_execution",
        lambda user_id, tool_name: log_calls.append((user_id, tool_name)),
    )
    from core.agents.tool_compactor import _audit_if_high_risk

    # guard 關閉
    monkeypatch.delenv("CONSENT_TOOL_GUARD_ENABLED", raising=False)
    wrapper = _make_wrapper_for_audit(
        risk_level="high", tool_name="submit_kyc_application", owner_id="u1"
    )
    _audit_if_high_risk(wrapper)
    assert len(log_calls) == 1
    assert log_calls[0] == ("u1", "submit_kyc_application")
