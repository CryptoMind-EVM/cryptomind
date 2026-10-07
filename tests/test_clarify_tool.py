"""clarify 工具 + Phase F（model-driven clarify）測試。

Hermes 式 model-driven 釐清（hybrid 設計）：
- 強模型主動呼叫 clarify 工具 → 工具回傳 {"__needs_clarify__": True, ...} 訊號
- claw_loop Phase F 偵測 used_tools 含 "clarify" → node-level interrupt 接手
- 弱模型不呼叫 → PR #317 的 should_clarify_wrong_scope rule 後衛兜底

本檔測三層：
1. clarify 工具本身（回傳訊號 JSON）
2. 訊號解析（is_clarify_signal / parse_clarify_signal / _extract_clarify_signal）
3. Phase F 偵測邏輯（used_tools 含 clarify → 觸發；不含 → 不觸發；已釐清 → 不重觸發）
"""
from __future__ import annotations

import json

from core.agents.manager.claw_loop import _extract_clarify_signal
from core.tools.clarify_tool import (
    clarify,
    is_clarify_signal,
    parse_clarify_signal,
)

# ============================================================================
# clarify 工具本身
# ============================================================================


def test_clarify_returns_signal_json():
    """clarify 工具回傳含 __needs_clarify__ 的 JSON 字串（不是純文字）。"""
    result = clarify.invoke(
        {"question": "你問的台股指哪一種？", "options": ["整體大盤", "特定股票"]}
    )
    assert isinstance(result, str)
    data = json.loads(result)
    assert data["__needs_clarify__"] is True
    assert data["question"] == "你問的台股指哪一種？"
    assert data["options"] == ["整體大盤", "特定股票"]


def test_clarify_without_options():
    """沒給 options 也要能回傳（options 選填）。"""
    result = clarify.invoke({"question": "你想投資什麼？"})
    data = json.loads(result)
    assert data["__needs_clarify__"] is True
    assert data["question"] == "你想投資什麼？"
    assert data["options"] == []


def test_clarify_has_descriptive_docstring():
    """docstring 是 LLM 看的工具描述，必須教模型何時呼叫。"""
    desc = clarify.description
    # 必須提到關鍵觸發情境（讓模型知道何時用）
    assert "台股" in desc or "美股" in desc or "範圍" in desc or "scope" in desc.lower()
    assert "釐清" in desc or "clarif" in desc.lower()


# ============================================================================
# 訊號解析
# ============================================================================


def test_is_clarify_signal_true_for_signal():
    """含 __needs_clarify__ + question 的字串 → True。"""
    signal = json.dumps(
        {"__needs_clarify__": True, "question": "x", "options": []}
    )
    assert is_clarify_signal(signal) is True


def test_is_clarify_signal_false_for_plain_text():
    """普通工具輸出（如價格）→ False。"""
    assert is_clarify_signal("BTC 目前價格 67000 美元") is False
    assert is_clarify_signal("台積電(2330)股價 700 元") is False


def test_is_clarify_signal_false_for_empty_or_none():
    assert is_clarify_signal("") is False
    assert is_clarify_signal(None) is False  # type: ignore[arg-type]


def test_parse_clarify_signal_returns_dict():
    """解析訊號回 dict（含 question/options）。"""
    signal = json.dumps(
        {
            "__needs_clarify__": True,
            "question": "指哪一種？",
            "options": ["A", "B"],
        }
    )
    parsed = parse_clarify_signal(signal)
    assert parsed["__needs_clarify__"] is True
    assert parsed["question"] == "指哪一種？"
    assert parsed["options"] == ["A", "B"]


def test_parse_clarify_signal_empty_for_non_signal():
    """非訊號字串 → 空 dict。"""
    assert parse_clarify_signal("普通文字") == {}
    assert parse_clarify_signal("") == {}
    assert parse_clarify_signal(None) == {}  # type: ignore[arg-type]


# ============================================================================
# _extract_clarify_signal（從 result.data 抽訊號）
# ============================================================================


def test_extract_signal_found_in_tool_outputs():
    """tool_outputs 含 clarify 訊號 → 抽出 {question, options}。"""
    signal = clarify.invoke(
        {"question": "台股指哪種？", "options": ["大盤", "個股"]}
    )
    result_data = {
        "used_tools": ["clarify", "get_crypto_price"],
        "tool_outputs": [signal, "BTC 67000"],
    }
    extracted = _extract_clarify_signal(result_data)
    assert extracted is not None
    assert extracted["question"] == "台股指哪種？"
    assert extracted["options"] == ["大盤", "個股"]


def test_extract_signal_none_when_no_clarify_output():
    """tool_outputs 不含 clarify 訊號 → None。"""
    result_data = {
        "used_tools": ["get_crypto_price"],
        "tool_outputs": ["BTC 67000"],
    }
    assert _extract_clarify_signal(result_data) is None


def test_extract_signal_handles_empty_or_missing():
    """缺 tool_outputs / 空 dict / None → None（不 crash）。"""
    assert _extract_clarify_signal({}) is None
    assert _extract_clarify_signal({"tool_outputs": []}) is None
    assert _extract_clarify_signal(None) is None  # type: ignore[arg-type]
    assert _extract_clarify_signal("not a dict") is None  # type: ignore[arg-type]


def test_extract_signal_picks_first_signal():
    """多個 tool_outputs 中第一個 clarify 訊號勝出。"""
    signal1 = clarify.invoke({"question": "第一個問題"})
    signal2 = clarify.invoke({"question": "第二個問題"})
    result_data = {"tool_outputs": [signal1, signal2]}
    extracted = _extract_clarify_signal(result_data)
    assert extracted["question"] == "第一個問題"


# ============================================================================
# Phase F 偵測邏輯（用 used_tools + _extract_clarify_signal 模擬）
# ============================================================================
# 注意：完整 Phase F 的 interrupt 測試需要 mock LangGraph 的 interrupt()，
# 且 claw_loop 是 async node——這裡只測「偵測訊號」這層的純邏輯（同步、可單測）。
# interrupt 觸發本身由既有 Phase D/E 的整合測試覆蓋（同模式）。


def test_phase_f_detection_logic_triggers_when_clarify_used():
    """模型呼叫了 clarify（used_tools 含）+ tool_outputs 有訊號 → 應觸發。

    模擬 claw_loop Phase F 的偵測條件：`"clarify" in used_tools and signal`。
    """
    signal = clarify.invoke({"question": "指哪種？"})
    used_tools = ["clarify"]
    result_data = {"used_tools": used_tools, "tool_outputs": [signal]}
    # 偵測條件（同 claw_loop Phase F）
    should_trigger = (
        "clarify" in used_tools
        and _extract_clarify_signal(result_data) is not None
    )
    assert should_trigger is True


def test_phase_f_detection_skips_when_clarify_not_used():
    """模型沒呼叫 clarify（used_tools 不含）→ 不觸發（rule 後衛接手）。"""
    used_tools = ["get_crypto_price"]
    result_data = {"used_tools": used_tools, "tool_outputs": ["BTC 67000"]}
    should_trigger = (
        "clarify" in used_tools
        and _extract_clarify_signal(result_data) is not None
    )
    assert should_trigger is False


def test_phase_f_detection_skips_when_already_clarified():
    """_tool_clarified=True（已釐清過）→ 不重觸發（防 resume 無限迴圈）。

    模擬 claw_loop Phase F 的 `_tool_clarified` state flag。
    """
    signal = clarify.invoke({"question": "指哪種？"})
    used_tools = ["clarify"]
    _tool_clarified = True  # 已釐清過
    result_data = {"used_tools": used_tools, "tool_outputs": [signal]}
    should_trigger = (
        not _tool_clarified
        and "clarify" in used_tools
        and _extract_clarify_signal(result_data) is not None
    )
    assert should_trigger is False


def test_phase_f_detection_skips_when_signal_missing():
    """used_tools 含 clarify 但 tool_outputs 訊號遺失（邊界）→ 不觸發。

    場景：模型呼叫了 clarify 但訊號沒進 tool_outputs（理論上不該發生，
    但防禦性處理——不該 crash 或誤觸發）。
    """
    used_tools = ["clarify"]
    result_data = {"used_tools": used_tools, "tool_outputs": []}
    should_trigger = (
        "clarify" in used_tools
        and _extract_clarify_signal(result_data) is not None
    )
    assert should_trigger is False
