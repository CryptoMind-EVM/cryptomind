"""補回 OpenAI 相容端點的 ``reasoning_content`` —— langchain 刻意不搬的那一欄。

## 為什麼要自己寫

DeepSeek 的串流 API 確實有送推理內容（實測 2026-09-06，raw SSE 的 delta key 是
``{'content', 'role', 'reasoning_content'}``，一題 286 字元），但經過
langchain-openai 之後 ``additional_kwargs`` 是空的。langchain 自己的 docstring
說明了原因：

    Non-standard response fields added by third-party providers (e.g.
    ``reasoning_content``) are not extracted. Use a provider-specific
    subclass for full provider support.

於是有兩個後果：

1. **前端看不到思考過程**。最後那次呼叫要先產 1000+ 個 reasoning token（≈9 秒）
   才吐第一個可見字元，使用者只看得到空白轉圈——手機截圖那個「卡很久才出字」。
2. **推理／不推理沒辦法混用**。歷史裡的 assistant 訊息沒有 ``reasoning_content``，
   下一個開推理的回合就會被 DeepSeek 擋：

       400 The `reasoning_content` in the thinking mode must be passed back to the API.

   這是 ``PHASED_MODEL_ENABLED``（#662）在 DeepSeek 上只能降級的原因。

## 為什麼不用 langchain-deepseek 套件

它只解了第 1 件（``chat_models.py`` 把 delta 的 ``reasoning_content`` 撈進
``additional_kwargs``），**回放沒做**——serialize 還是繼承 ChatOpenAI 的
``_convert_message_to_dict``，一樣會把欄位丟掉。裝了還是解不掉 400，卻多一個依賴。
這裡兩個 hook 一起補，順便涵蓋 OpenRouter／NVIDIA 的 ``reasoning`` 別名。

## 回放規則

參考 deepseek-ai/deepseek-harness 的
``2026-08-19-deepseek-reasoning-passback-every-turn``：**每個帶推理的 assistant
回合都要回放**，不分有沒有工具呼叫，而且回放文字要跟串流下發的逐字一致
（他們靠雜湊比對重建對話）。沒有推理的回合不發這個欄位，行為不變。

前綴穩定性：新增的文字固定在該回合的位置、之後每次請求都相同，所以 prompt
前綴仍然穩定，只有跨越這次變更的第一個請求會從那個位置起失去快取複用。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

#: delta 裡推理內容的欄位名。DeepSeek 用 ``reasoning_content``，
#: OpenRouter／NVIDIA 轉發時用 ``reasoning``。
_REASONING_KEYS = ("reasoning_content", "reasoning")

#: content block 形式的推理。官方 SDK（Anthropic / Gemini / OpenAI o 系列）
#: 走的是這條：推理是 message.content 裡的一個 block，不是 additional_kwargs。
#: 這些 block 沒有 ``text`` 鍵，所以既有的正文組裝
#:（``part.get("text", "")``）不會把它們誤當答案——只是思考區塊會空白。
_REASONING_BLOCK_TYPES = ("thinking", "reasoning", "reasoning_content")


def _from_blocks(content: Any) -> str:
    """從 content blocks 撈推理文字。

    形狀依 provider 不同：Anthropic 是 ``{"type":"thinking","thinking":…}``，
    OpenAI o 系列是 ``{"type":"reasoning", ...}``，Gemini 的 part 帶 ``thought``。
    取值時依序試 block 的同名鍵、``text``、``thought``。
    """
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in _REASONING_BLOCK_TYPES:
            value = block.get(btype) or block.get("text") or ""
        elif block.get("thought"):
            # Gemini：thought=True 標記這個 part 是思考，文字仍在 text
            value = block.get("text") or ""
        else:
            continue
        if value:
            parts.append(str(value))
    return "".join(parts)


def extract_reasoning(source: Any) -> str:
    """撈出推理文字，沒有就回空字串。

    三種來源都認：串流 delta 的 dict、message 的 ``additional_kwargs``
    （OpenAI 相容端點），以及 message.content 的 block（官方 SDK）。
    """
    if isinstance(source, dict):
        for key in _REASONING_KEYS:
            value = source.get(key)
            if value:
                return str(value)
        return ""
    kwargs = getattr(source, "additional_kwargs", None) or {}
    for key in _REASONING_KEYS:
        value = kwargs.get(key)
        if value:
            return str(value)
    return _from_blocks(getattr(source, "content", None))


class ReasoningAwareChatOpenAI(ChatOpenAI):
    """ChatOpenAI 加上 ``reasoning_content`` 的接收與回放。

    對不送這個欄位的端點（OpenAI 官方等）完全是 no-op——兩個 hook 都只在真的
    有推理內容時才動作。
    """

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
    ):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None
        choices = chunk.get("choices") or []
        if not choices:
            return generation_chunk
        reasoning = extract_reasoning(choices[0].get("delta") or {})
        if reasoning:
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk

    def _get_request_payload(
        self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # 把歷史裡 AIMessage 的推理文字放回 wire format。super() 已經把
        # messages 攤平成 dict，順序與 input 的 message 順序一致，所以按序
        # 對位；對不上就整段跳過，寧可不回放也不要放錯回合。
        try:
            messages = payload.get("messages") or []
            originals = [
                m for m in _iter_messages(input_) if isinstance(m, AIMessage)
            ]
            assistants = [m for m in messages if m.get("role") == "assistant"]
            if len(originals) != len(assistants):
                return payload
            for original, wire in zip(originals, assistants):
                reasoning = extract_reasoning(original)
                if reasoning:
                    wire["reasoning_content"] = reasoning
        except (AttributeError, TypeError) as e:
            # 回放失敗只會退回「沒有推理歷史」的既有行為，不該讓請求整個掛掉
            logger.warning("[ReasoningClient] reasoning 回放略過: %s", e)
        return payload


def _iter_messages(input_: Any):
    """把 ``_get_request_payload`` 收到的各種 input 形狀攤成 message 序列。"""
    if isinstance(input_, list):
        return input_
    if hasattr(input_, "to_messages"):
        return input_.to_messages()
    if hasattr(input_, "messages"):
        return input_.messages
    return []


def build_chat_model(**kwargs: Any):
    """``init_chat_model`` 的替身：OpenAI 相容第三方端點改用推理感知的 client。

    只有 ``model_provider == "openai"`` **且**有 ``base_url``（＝DeepSeek /
    OpenRouter / NVIDIA / Groq 這類相容端點）才換；OpenAI 官方與其他 SDK
    （anthropic、google…）原樣走 ``init_chat_model``，行為不變。
    """
    from langchain.chat_models import init_chat_model

    if kwargs.get("model_provider") != "openai" or not kwargs.get("base_url"):
        return init_chat_model(**kwargs)

    mapped = {k: v for k, v in kwargs.items() if k != "model_provider"}
    try:
        return ReasoningAwareChatOpenAI(**mapped)
    except Exception as e:
        # 參數對不上就退回原本的路徑，不要因為想拿推理內容而讓聊天整個不能用
        logger.warning("[ReasoningClient] 建立失敗，退回 init_chat_model: %s", e)
        return init_chat_model(**kwargs)
