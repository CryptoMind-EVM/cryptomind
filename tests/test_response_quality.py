"""測試 CLAW 回應品質機制：system prompt 引導 + runtime nudge。

對齊 Hermes / OpenClaw 的設計：
1. system prompt 含 response_quality（執行原則 + 回答指引）
2. 工具後若模型回 empty → 注入 nudge 重跑一輪（只在 empty 時觸發，只重試一次）
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from core.agents.agents.cryptomind_agent import CryptoMindAgent
from core.agents.models import SubTask


def _make_agent():
    agent = CryptoMindAgent(llm_client=MagicMock(), tool_registry=None)
    agent._get_tool_metas = MagicMock(return_value=[])
    return agent


# ---------------------------------------------------------------------------
# system prompt 含 response_quality
# ---------------------------------------------------------------------------


def test_response_quality_prompt_present():
    """system prompt 必須包含回應原則區塊（防回歸）。"""
    from core.agents.prompt_registry import PromptRegistry

    PromptRegistry.load()
    agent = _make_agent()
    prompt = agent._get_system_prompt("zh-TW")
    # 核心標記必須存在
    assert "工具輸出是**證據，不是答案**" in prompt, (
        "system prompt 缺少 response_quality 區塊 — 這會導致模型只倒工具結果"
    )
    assert "先判斷請求類型" in prompt
    # 英文版
    prompt_en = agent._get_system_prompt("en")
    assert "evidence, not the answer" in prompt_en
    assert "Classify the request" in prompt_en


# ---------------------------------------------------------------------------
# runtime nudge — 工具後 empty 回應恢復
# ---------------------------------------------------------------------------


def test_empty_reply_after_tools_triggers_nudge():
    """工具後模型回 empty → nudge 重跑一輪 → 拿到答案。

    模擬 Hermes/OpenClaw 的 post-tool-empty-recovery：模型執行了工具但沒
    產生最終回應，注入 nudge user message 帶工具歷史重跑。
    """
    agent = _make_agent()
    astream_call_count = {"n": 0}

    async def fake_astream(_input, stream_mode=None):
        astream_call_count["n"] += 1
        if astream_call_count["n"] == 1:
            # 第一輪：工具被呼叫，但最後一條是 ToolMessage（無 AIMessage 回應）
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {"name": "get_crypto_price", "args": "", "id": "1", "index": 0}
                        ],
                    ),
                    {},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(
                        content="0.0271", tool_call_id="1", name="get_crypto_price"
                    ),
                    {},
                ),
            )
            # values 只含到 ToolMessage 為止（模型沒回應就停了）
            yield (
                "values",
                {
                    "messages": [
                        ToolMessage(
                            content="0.0271", tool_call_id="1", name="get_crypto_price"
                        )
                    ]
                },
            )
        else:
            # 第二輪（nudge 後）：正常回應
            yield ("messages", (AIMessageChunk(content="FLOW 現價 "), {}))
            yield ("messages", (AIMessageChunk(content="$0.0271"), {}))
            yield ("values", {"messages": [AIMessage(content="FLOW 現價 $0.0271")]})

    fake_graph = SimpleNamespace(astream=fake_astream)
    with patch(
        "core.agents.base_react_agent.create_agent", return_value=fake_graph
    ):
        result = asyncio.run(
            agent.execute_streaming(
                SubTask(step=0, description="FLOW 價格", agent="cryptomind", context={})
            )
        )

    assert astream_call_count["n"] == 2, "nudge 應觸發第二輪 astream"
    assert result.success is True
    assert "0.0271" in result.message, "nudge 後應拿到正常答案"


def test_nonempty_reply_does_not_trigger_nudge():
    """模型有出字（即使只是列數據）→ nudge 不觸發（不浪費 token）。

    這是兩家 CLAW 的共識：nudge 只在完全 empty 時觸發。
    """
    agent = _make_agent()
    astream_call_count = {"n": 0}

    async def fake_astream(_input, stream_mode=None):
        astream_call_count["n"] += 1
        # 模型有回應（雖然只是列價格，沒回答「值不值得買」）
        yield (
            "messages",
            (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": "get_crypto_price", "args": "", "id": "1", "index": 0}
                    ],
                ),
                {},
            ),
        )
        yield (
            "messages",
            (
                ToolMessage(
                    content="0.0271", tool_call_id="1", name="get_crypto_price"
                ),
                {},
            ),
        )
        yield ("messages", (AIMessageChunk(content="目前價格 $0.0271"), {}))
        yield ("values", {"messages": [AIMessage(content="目前價格 $0.0271")]})

    fake_graph = SimpleNamespace(astream=fake_astream)
    with patch(
        "core.agents.base_react_agent.create_agent", return_value=fake_graph
    ):
        result = asyncio.run(
            agent.execute_streaming(
                SubTask(step=0, description="FLOW 值得買嗎", agent="cryptomind", context={})
            )
        )

    assert astream_call_count["n"] == 1, "有出字不應觸發 nudge"
    assert result.success is True
    assert result.message == "目前價格 $0.0271"


def test_nudge_only_retries_once():
    """nudge 第二輪仍 empty → 只重試一次就放棄（不無限重試）。"""
    agent = _make_agent()
    astream_call_count = {"n": 0}

    async def fake_astream(_input, stream_mode=None):
        astream_call_count["n"] += 1
        # 每一輪都只回工具結果，永遠 empty
        yield (
            "messages",
            (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": "get_crypto_price", "args": "", "id": "1", "index": 0}
                    ],
                ),
                {},
            ),
        )
        yield (
            "messages",
            (
                ToolMessage(
                    content="0.0271", tool_call_id="1", name="get_crypto_price"
                ),
                {},
            ),
        )
        yield (
            "values",
            {
                "messages": [
                    ToolMessage(
                        content="0.0271", tool_call_id="1", name="get_crypto_price"
                    )
                ]
            },
        )

    fake_graph = SimpleNamespace(astream=fake_astream)
    with patch(
        "core.agents.base_react_agent.create_agent", return_value=fake_graph
    ):
        result = asyncio.run(
            agent.execute_streaming(
                SubTask(step=0, description="FLOW 價格", agent="cryptomind", context={})
            )
        )

    assert astream_call_count["n"] == 2, "只重試一次（首輪 + nudge 一輪），不更多"
    assert result.success is False
    # 空回應 fallback 必須本地化（不是硬編碼英文）。zh-TW 是預設 language。
    assert result.message != "No response generated."
    from core.agents.base_react_agent import empty_response_message

    assert result.message == empty_response_message("zh-TW")


def test_no_tools_no_nudge():
    """沒呼叫工具（純閒聊）+ empty → 不觸發 nudge（nudge 是工具後專屬）。"""
    agent = _make_agent()
    astream_call_count = {"n": 0}

    async def fake_astream(_input, stream_mode=None):
        astream_call_count["n"] += 1
        # 純 empty，無任何工具
        yield ("values", {"messages": []})

    fake_graph = SimpleNamespace(astream=fake_astream)
    with patch(
        "core.agents.base_react_agent.create_agent", return_value=fake_graph
    ):
        result = asyncio.run(
            agent.execute_streaming(
                SubTask(step=0, description="你好", agent="cryptomind", context={})
            )
        )

    assert astream_call_count["n"] == 1, "無工具 empty 不觸發 nudge"
    assert result.success is False


# ---------------------------------------------------------------------------
# 空回應與錯誤 fallback 必須完整在地化（zh-TW / zh-CN / en / ru）
# ---------------------------------------------------------------------------


def test_empty_response_message_all_languages():
    """empty_response_message() 在 4 種語言各回對應語言，且含關鍵字（非英文 fallback）。"""
    from core.agents.base_react_agent import empty_response_message

    msgs = {lang: empty_response_message(lang) for lang in ("zh-TW", "zh-CN", "en", "ru")}
    # 每種語言都應該不同
    assert len(set(msgs.values())) == 4, "4 種語言訊息不應重複"
    # 不應是硬編碼英文
    assert msgs["ru"] != "No response generated."
    assert msgs["en"] != msgs["ru"]
    # 中文版本應含中文（繁體「回應」/ 簡體「回应」）
    assert "回應" in msgs["zh-TW"]
    assert "回应" in msgs["zh-CN"]
    # 俄語應含西里爾字母
    assert any("\u0400" <= ch <= "\u04ff" for ch in msgs["ru"])


def test_empty_response_streaming_localized_per_language():
    """串流版空回應 fallback 依 context.language 切換語言，不是一律英文。"""
    from core.agents.base_react_agent import empty_response_message

    for lang in ("zh-CN", "en", "ru"):
        agent = _make_agent()

        async def fake_astream(_input, stream_mode=None):
            yield ("values", {"messages": []})

        fake_graph = SimpleNamespace(astream=fake_astream)
        with patch(
            "core.agents.base_react_agent.create_agent", return_value=fake_graph
        ):
            result = asyncio.run(
                agent.execute_streaming(
                    SubTask(
                        step=0, description="hello", agent="cryptomind",
                        context={"language": lang},
                    )
                )
            )
        assert result.success is False
        assert result.message == empty_response_message(lang), (
            f"language={lang} 的空回應 fallback 不正確"
        )


def test_friendly_llm_error_supports_russian():
    """_friendly_llm_error 傳 ru 時應回俄語，不是落回英文。"""
    from core.agents.base_react_agent import _friendly_llm_error

    ru_429 = _friendly_llm_error("Error code: 429 - rate limit exceeded", "ru")
    assert ru_429 is not None, "429 應能比對到友善訊息"
    # 必須含西里爾字母（俄語），而非英文
    assert any("\u0400" <= ch <= "\u04ff" for ch in ru_429), (
        "ru 的 429 訊息應為俄語，不應落回英文"
    )
    assert ru_429 != _friendly_llm_error("Error code: 429 - rate limit exceeded", "en")


# ============================================================================
# NVIDIA NIM ResourceExhausted 友善訊息（2026-07-20 修復）
#
# 過去 NIM 端 ``ResourceExhausted: Worker local total request limit reached
# (16/16)`` 因沒被 _LLM_ERROR_HINTS 認得，原文塞進 generic_error 噴進聊天泡泡。
# ============================================================================


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_friendly_llm_error_recognizes_nim_resource_exhausted(lang):
    """NIM ``ResourceExhausted`` 必須比對到 NIM 專屬友善訊息（4 語）。"""
    from core.agents.base_react_agent import _friendly_llm_error

    msg = _friendly_llm_error(
        "ResourceExhausted: Worker local total request limit reached (16/16)",
        lang,
    )
    assert msg is not None, "NIM ResourceExhausted 應能比對到友善訊息"
    # 不可再含原文關鍵字（過去的 bug）
    assert "ResourceExhausted" not in msg
    assert "16/16" not in msg


def test_friendly_llm_error_nim_message_differs_from_429():
    """NIM 並發上限 vs HTTP 429 quota 是不同情況，訊息必須不同。

    NIM 並發：等 30 秒就有 slot；429 quota：通常要等方案重置。
    """
    from core.agents.base_react_agent import _friendly_llm_error

    nim_msg = _friendly_llm_error(
        "ResourceExhausted: Worker local total request limit reached (16/16)",
        "zh-TW",
    )
    quota_msg = _friendly_llm_error("Error code: 429 - rate limit exceeded", "zh-TW")
    assert nim_msg != quota_msg, (
        "NIM 並發上限與 HTTP 429 quota 應有不同訊息（使用者行動不同）"
    )


def test_is_rate_limit_error_classifies_nim_correctly():
    """rate_limit.is_rate_limit_error 應認得 NIM 並回 True（可 retry）。"""
    from core.agents.rate_limit import is_rate_limit_error

    assert is_rate_limit_error(
        Exception("ResourceExhausted: Worker local total request limit reached (16/16)")
    )
    assert is_rate_limit_error(Exception("Error code: 429 - rate limit"))
    # 認證錯誤不該被歸類為 rate limit
    assert not is_rate_limit_error(Exception("Error code: 401 - invalid api key"))
    assert not is_rate_limit_error(Exception("Error code: 402 - insufficient credits"))
