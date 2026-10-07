"""事實抽取「有訊號才抽」（2026-09-12）：純行情問題不再每輪多打一次 LLM。

2026-09-14 擴充：對 AI 行為的糾正／工作方式指示（「以後先查證再回答」）也算
可留存的記憶訊號——之前這類訊息被閘門外的抽取 prompt 限定投資畫像而丟掉，
導致同類錯誤重複發生（ENS 舊聞當近期事件事件）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.database.memory import (
    MemoryStore,
    _filter_current_turn_facts,
    should_extract_facts,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "msg",
    [
        "我偏好低風險資產。",
        "我持有 0.5 顆 BTC，想長期放",
        "記住我每個月定投 1 萬",
        "I prefer conservative ETFs",
        "remember that I hold TSMC",
        "以後回答用英文",
    ],
)
def test_personal_statements_are_extracted(msg):
    assert should_extract_facts(msg, mode="signal")


@pytest.mark.parametrize(
    "msg",
    [
        "BTC 現在多少錢？",
        "台積電本益比多少",
        "ETH 技術面怎麼看",
        "What is NVDA trading at?",
        "你好",
        "",
    ],
)
def test_market_questions_are_skipped(msg):
    assert not should_extract_facts(msg, mode="signal")


@pytest.mark.parametrize(
    "msg",
    [
        "合約安全怎麼查證？",
        "台股財報去哪裡核實？",
    ],
)
def test_tool_consultation_with_feedback_word_is_skipped(msg):
    """有查證类字詞但沒有指向 AI 的受詞 → 是工具諮詢，不是糾正，不抽。"""
    assert not should_extract_facts(msg, mode="signal")


@pytest.mark.parametrize(
    "msg",
    [
        "你要再三查證再回答，不要亂回答",
        "你剛剛把舊聞當近期事件，以後回答前先查證",
        "回答前先核實資料來源",
        "你不要亂編日期",
        "don't guess — double-check your dates before answering",
        "stop guessing, verify before you answer",
    ],
)
def test_behavioral_feedback_is_extracted(msg):
    """對 AI 行為的糾正／指示必須觸發抽取——否則教訓留不下來，同錯重犯。"""
    assert should_extract_facts(msg, mode="signal")


def test_modes_always_and_off():
    assert should_extract_facts("BTC 多少錢", mode="always")
    assert not should_extract_facts("記住我偏好低風險", mode="off")


def test_env_default_is_signal(monkeypatch):
    monkeypatch.delenv("MEMORY_FACT_EXTRACTION", raising=False)
    assert not should_extract_facts("BTC 多少錢")
    monkeypatch.setenv("MEMORY_FACT_EXTRACTION", "always")
    assert should_extract_facts("BTC 多少錢")


@pytest.mark.asyncio
async def test_extract_facts_from_turn_skips_llm_without_signal(monkeypatch):
    monkeypatch.delenv("MEMORY_FACT_EXTRACTION", raising=False)
    store = MemoryStore("memory-user")
    llm = MagicMock()
    with (
        patch.object(store, "facts_to_text", return_value=""),
        patch.object(store, "write_facts"),
    ):
        ok = await store.extract_facts_from_turn(
            user_message="BTC 現在多少錢？",
            assistant_message="61,234",
            turn_index=1,
            llm=llm,
        )
    assert ok is False
    llm.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_extract_facts_from_turn_calls_llm_with_signal(monkeypatch):
    monkeypatch.delenv("MEMORY_FACT_EXTRACTION", raising=False)
    store = MemoryStore("memory-user")
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content='{"facts": []}')
    with (
        patch.object(store, "facts_to_text", return_value=""),
        patch.object(store, "write_facts"),
    ):
        await store.extract_facts_from_turn(
            user_message="我偏好低風險資產。",
            assistant_message="好的",
            turn_index=1,
            llm=llm,
        )
    llm.invoke.assert_called_once()


# ── 2026-09-14：抽取 prompt 必須涵蓋「使用者對 AI 行為的指示」───────────────


@pytest.mark.asyncio
async def test_extraction_prompt_covers_user_instructions(monkeypatch):
    """抽取 prompt 必須教 LLM 收「行為指示」類記憶（category=context）——
    否則「以後要再三查證」這類教訓永遠進不了記憶。"""
    monkeypatch.delenv("MEMORY_FACT_EXTRACTION", raising=False)
    store = MemoryStore("memory-user")
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content='{"facts": []}')
    with (
        patch.object(store, "facts_to_text", return_value=""),
        patch.object(store, "write_facts"),
    ):
        await store.extract_facts_from_turn(
            user_message="以後回答前先查證日期",
            assistant_message="好的",
            turn_index=1,
            llm=llm,
        )
    prompt = llm.invoke.call_args[0][0][0].content
    assert "context" in prompt, "抽取 prompt 缺 category=context 指引"
    assert "instruction" in prompt.lower(), "抽取 prompt 缺行為指示的說明"


def test_filter_current_turn_facts_keeps_category():
    """合法 category 原樣保留、非法 category 落回 fact、低信心照舊丟棄。"""
    facts = [
        {
            "key": "answer_style",
            "value": "使用者要求：回答前先交叉查證日期",
            "source_turn": 3,
            "confidence": "high",
            "category": "context",
        },
        {
            "key": "weird_cat",
            "value": "x",
            "source_turn": 3,
            "confidence": "high",
            "category": "weird",
        },
        {
            "key": "low_conf",
            "value": "x",
            "source_turn": 3,
            "confidence": "low",
        },
    ]
    out = _filter_current_turn_facts(facts, 3)
    by_key = {f["key"]: f for f in out}
    assert by_key["answer_style"]["category"] == "context"
    assert by_key["weird_cat"]["category"] == "fact"
    assert "low_conf" not in by_key
