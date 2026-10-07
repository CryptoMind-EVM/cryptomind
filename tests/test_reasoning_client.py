"""``reasoning_content`` 的接收與回放守衛。

langchain-openai 刻意不搬 provider 專屬欄位（它自己的 docstring 就這麼寫），
所以這兩件事只要有人「順手」改回用 ChatOpenAI／init_chat_model 就會靜默失效：
思考區塊變空白、分階段模型綁定又開始 400。這裡把兩個 hook 都釘住。
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from core.agents.reasoning_client import (
    ReasoningAwareChatOpenAI,
    build_chat_model,
    extract_reasoning,
)


def _client(**kw):
    return ReasoningAwareChatOpenAI(
        model="deepseek-v4-flash",
        api_key="sk-test-not-a-real-key",
        base_url="https://api.deepseek.com",
        **kw,
    )


# ── extract_reasoning ───────────────────────────────────────────────────


def test_extracts_deepseek_key_from_delta():
    assert extract_reasoning({"reasoning_content": "想一下"}) == "想一下"


def test_extracts_openrouter_alias_from_delta():
    """OpenRouter／NVIDIA 轉發時欄位叫 reasoning，不是 reasoning_content。"""
    assert extract_reasoning({"reasoning": "thinking"}) == "thinking"


def test_extracts_from_message_additional_kwargs():
    msg = AIMessage(content="", additional_kwargs={"reasoning_content": "嗯"})
    assert extract_reasoning(msg) == "嗯"


def test_no_reasoning_returns_empty_string_not_none():
    """回 None 的話呼叫端的 `if reasoning:` 還是對，但字串串接會炸。"""
    assert extract_reasoning({}) == ""
    assert extract_reasoning(AIMessage(content="hi")) == ""


# ── ① 串流接收 ──────────────────────────────────────────────────────────


def test_stream_chunk_carries_reasoning_into_additional_kwargs():
    """沒有這個，前端的思考區塊永遠是空的（langchain 預設把它丟掉）。"""
    llm = _client()
    chunk = {
        "choices": [{"delta": {"role": "assistant", "reasoning_content": "先算跌幅"}}]
    }
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.additional_kwargs.get("reasoning_content") == "先算跌幅"


def test_stream_chunk_without_reasoning_is_untouched():
    llm = _client()
    chunk = {"choices": [{"delta": {"role": "assistant", "content": "答"}}]}
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert "reasoning_content" not in (gen.message.additional_kwargs or {})


def test_empty_choices_does_not_crash():
    llm = _client()
    assert llm._convert_chunk_to_generation_chunk({"choices": []}, AIMessageChunk, None) is not None


# ── ② 回放 ──────────────────────────────────────────────────────────────


def _assistants(payload):
    return [m for m in payload["messages"] if m.get("role") == "assistant"]


def test_replay_puts_reasoning_back_on_the_wire():
    """DeepSeek 要求思考模式下歷史的 reasoning_content 要回傳，否則整段對話 400。"""
    llm = _client()
    msgs = [
        HumanMessage(content="BTC 多少？"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "要先查價"},
            tool_calls=[{"name": "get_price", "args": {}, "id": "c1"}],
        ),
        ToolMessage(content="90000", tool_call_id="c1"),
        HumanMessage(content="那 ETH？"),
    ]
    payload = llm._get_request_payload(msgs)
    assert _assistants(payload)[0].get("reasoning_content") == "要先查價"


def test_replay_covers_turns_without_tool_calls():
    """deepseek-harness 的結論：每個帶推理的回合都要回放，不分有沒有工具呼叫。

    只在有工具呼叫的回合回放，會讓純作答回合到達轉發網關時沒有思維鏈，
    簽名查找落空、對話重建分岔——而且是偶發，最難查。
    """
    llm = _client()
    msgs = [
        HumanMessage(content="嗨"),
        AIMessage(content="你好", additional_kwargs={"reasoning_content": "打招呼"}),
        HumanMessage(content="再問一次"),
    ]
    payload = llm._get_request_payload(msgs)
    assert _assistants(payload)[0].get("reasoning_content") == "打招呼"


def test_turns_without_reasoning_do_not_get_the_field():
    """沒推理的回合不該憑空長出這個欄位——那會多送 token 也可能被 provider 拒。"""
    llm = _client()
    msgs = [HumanMessage(content="嗨"), AIMessage(content="你好")]
    payload = llm._get_request_payload(msgs)
    assert "reasoning_content" not in _assistants(payload)[0]


def test_replay_is_byte_identical_to_what_was_streamed():
    """回放文字必須與下發逐字一致（轉發網關靠雜湊比對重建對話）。"""
    llm = _client()
    streamed = "第一段" + "第二段" + "第三段"
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="a", additional_kwargs={"reasoning_content": streamed}),
    ]
    payload = llm._get_request_payload(msgs)
    assert _assistants(payload)[0]["reasoning_content"] == streamed


def test_payload_is_json_serialisable():
    """多塞的欄位要能過 json.dumps，否則送出時才炸。"""
    llm = _client()
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="a", additional_kwargs={"reasoning_content": "想"}),
    ]
    json.dumps(llm._get_request_payload(msgs), default=str)


# ── build_chat_model 的路由 ─────────────────────────────────────────────


def test_compatible_endpoint_gets_reasoning_aware_client():
    llm = build_chat_model(
        model="deepseek-v4-flash",
        model_provider="openai",
        base_url="https://api.deepseek.com",
        api_key="sk-test-not-a-real-key",
    )
    assert isinstance(llm, ReasoningAwareChatOpenAI)


def test_official_openai_without_base_url_is_untouched():
    """官方端點不送這個欄位，換 client 只會多一層沒必要的差異。"""
    llm = build_chat_model(
        model="gpt-5.4-mini",
        model_provider="openai",
        api_key="sk-test-not-a-real-key",
    )
    assert not isinstance(llm, ReasoningAwareChatOpenAI)


@pytest.mark.parametrize("provider", ["anthropic", "google_genai"])
def test_other_sdks_are_untouched(provider, monkeypatch):
    """非 OpenAI SDK 走原本的 init_chat_model，別把它們也換掉。"""
    import langchain.chat_models as lcm

    called = {}
    sentinel = object()

    def fake_init(**kw):
        called.update(kw)
        return sentinel

    monkeypatch.setattr(lcm, "init_chat_model", fake_init)
    out = build_chat_model(
        model="m", model_provider=provider, base_url="https://x", api_key="k"
    )
    assert out is sentinel
    assert called.get("model_provider") == provider


# ── content block 形式的推理（官方 SDK）────────────────────────────────
#
# Anthropic / Gemini / OpenAI o 系列不走 additional_kwargs，推理是
# message.content 裡的一個 block。只認 additional_kwargs 的話，這三家的
# 思考區塊會永遠空白——不會噴錯，只是沒東西。


def test_anthropic_thinking_block():
    msg = AIMessage(
        content=[
            {"type": "thinking", "thinking": "先拆解問題", "signature": "sig"},
            {"type": "text", "text": "答案是 A"},
        ]
    )
    assert extract_reasoning(msg) == "先拆解問題"


def test_openai_reasoning_block():
    msg = AIMessage(content=[{"type": "reasoning", "reasoning": "推導中"}])
    assert extract_reasoning(msg) == "推導中"


def test_gemini_thought_part():
    """Gemini 用 thought=True 標記，文字仍在 text 欄。"""
    msg = AIMessage(
        content=[
            {"type": "text", "text": "內部思考", "thought": True},
            {"type": "text", "text": "對外答案"},
        ]
    )
    assert extract_reasoning(msg) == "內部思考"


def test_multiple_reasoning_blocks_are_concatenated_in_order():
    msg = AIMessage(
        content=[
            {"type": "thinking", "thinking": "第一段"},
            {"type": "thinking", "thinking": "第二段"},
        ]
    )
    assert extract_reasoning(msg) == "第一段第二段"


def test_plain_text_blocks_are_not_treated_as_reasoning():
    """把正文當推理會讓答案在思考區塊裡重複一次。"""
    msg = AIMessage(content=[{"type": "text", "text": "這是答案"}])
    assert extract_reasoning(msg) == ""


def test_string_content_is_not_scanned_as_blocks():
    assert extract_reasoning(AIMessage(content="就是一段字")) == ""


def test_additional_kwargs_wins_over_blocks():
    """相容端點兩邊都有值時以 additional_kwargs 為準（那是逐 chunk 累積的來源）。"""
    msg = AIMessage(
        content=[{"type": "thinking", "thinking": "block 版"}],
        additional_kwargs={"reasoning_content": "kwargs 版"},
    )
    assert extract_reasoning(msg) == "kwargs 版"
