"""
用戶 LLM Client 工廠
根據用戶提供的 API key 創建 LLM 客戶端
⭐ 重要：完全使用用戶提供的 key，不從 .env 讀取
"""

import asyncio
import logging
import socket
from typing import Any, Optional

from langchain_core.messages import HumanMessage

from core.model_config import (
    get_default_model,
    get_provider_runtime,
    resolve_model_alias,
)

logger = logging.getLogger(__name__)

# 聊天/分析回覆的輸出 token 上限。避免未指定時被 OpenRouter 等以模型上限
# (常達 65536)預扣額度，導致免費/低餘額帳號 402。
#
# 2026-07-19：4096 → 8192。原本 4096 太保守，多標的比較 + 表格 + 技術分析
# 容易被截斷。8K 是多數 provider free tier 的安全上限（NVIDIA NIM、OpenRouter
# free tier 多為 8K-32K），且 claw_loop 現在有 finish_reason='length' 截斷偵測。
CHAT_MAX_OUTPUT_TOKENS = 8192

# Fallback provider 專用上限。fallback 通常用較強的 server-side 模型（如
# gpt-4o-mini），保留多一點空間產生完整回應。略小於 CHAT_MAX_OUTPUT_TOKENS
# 以保留預扣緩衝。
CHAT_MAX_OUTPUT_TOKENS_FALLBACK = 6144


def create_user_llm_client(
    provider: str,
    api_key: str,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Any:
    """
    根據用戶提供的 key 創建 LLM 客戶端 (LangChain BaseChatModel)

    Args:
        provider: "openai", "google_gemini", "anthropic", "deepseek",
                  "siliconflow", "groq", "openrouter"
        api_key: 用戶的 API key
        model: 用戶選擇的模型名稱（可選，未提供則使用 provider 預設值）
        max_tokens: 自訂輸出 token 上限（可選，預設用 CHAT_MAX_OUTPUT_TOKENS=8192）。
                    fallback provider 可傳 CHAT_MAX_OUTPUT_TOKENS_FALLBACK。

    Returns:
        配置好的 LLM 客戶端 (BaseChatModel)

    Raises:
        ValueError: 如果 provider 不支援或 api_key 為空
    """
    # 路由完全來自 PROVIDER_REGISTRY（單一真實來源）：
    # lc_provider 決定走哪個 LangChain SDK，base_url 為 OpenAI 相容端點（官方 SDK 留 None）。
    runtime = get_provider_runtime(provider)
    if not runtime:
        raise ValueError(f"不支援的 provider: {provider}")

    if (not api_key or not api_key.strip()) and runtime.get("api_key_optional"):
        api_key = "local"  # 平台自架 llama-server 不驗 key；SDK 要求非空字串
    if not api_key or not api_key.strip():
        raise ValueError("API key 不能為空")

    api_key = api_key.strip()

    lc_provider = runtime["lc_provider"]
    base_url = runtime["base_url"]

    requested_model = model or get_default_model(provider)
    resolved_model = resolve_model_alias(provider, requested_model)
    if resolved_model != requested_model:
        logger.info(
            "[LLM] %s 模型 %s 已下架，改用 %s",
            provider,
            requested_model,
            resolved_model,
        )
    resolved_max_tokens = max_tokens or CHAT_MAX_OUTPUT_TOKENS

    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "model_provider": lc_provider,
        "temperature": 0.5,
        "api_key": api_key,
        # 明確上限輸出 token。未指定時 OpenRouter 等會以「模型上限」(常達 65536)
        # 預扣額度，免費/低餘額帳號直接 402「付不起」。一般分析回覆 8192 token
        # 已充足（多標的比較 + 表格），且大幅降低每次請求的預扣成本。
        "max_tokens": resolved_max_tokens,
        # 降低重試次數：一次請求會呼叫多個 LLM(意圖/任務/反思/合成)，預設每次
        # 對 429 退避重試 2 次會疊加成數十秒 → Telegram 端誤判「回覆時間過長」。
        # 保留 1 次重試容忍暫時性抖動，但讓配額/速率限制更快浮現清楚訊息。
        "max_retries": 1,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if runtime.get("request_extra_body"):
        # 端點專屬的請求欄位（例如本地 llama-server 關 thinking），跟著每個請求送
        kwargs["extra_body"] = dict(runtime["request_extra_body"])

    # OpenAI 相容的第三方端點改走推理感知 client：langchain-openai 會把
    # reasoning_content 丟掉，前端就看不到思考過程，推理／不推理也沒法混用。
    from core.agents.reasoning_client import build_chat_model

    return build_chat_model(**kwargs)


def explain_llm_exception(exc: Exception) -> str:
    """將底層 LLM 例外轉為可診斷的訊息。"""
    chain = []
    current = exc
    seen = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )

    messages = " | ".join(str(item) for item in chain if str(item))

    if any(isinstance(item, socket.gaierror) for item in chain) or (
        "nodename nor servname provided" in messages
        or "Name or service not known" in messages
        or "Temporary failure in name resolution" in messages
    ):
        return "LLM 連線失敗：DNS 解析失敗，請檢查目前環境是否可連外網。"

    lowered_messages = messages.lower()
    if (
        "401" in messages
        or "unauthorized" in lowered_messages
        or "missing authentication header" in lowered_messages
        or "invalid api key" in lowered_messages
    ):
        return "API Key 無效或已過期"

    if "429" in messages:
        # 免費層速率限制最常見（如 NVIDIA NIM 每分鐘請求數上限），
        # 「配額不足」會誤導使用者以為要儲值——實際等一下就好。
        return "請求過於頻繁（429 速率限制），請稍等約一分鐘再試"

    if (
        "Connection error" in messages
        or "ConnectError" in messages
        or "APIConnectionError" in messages
    ):
        return "LLM 連線失敗：無法連到模型提供商，請檢查外網連線、防火牆或代理設定。"

    return f"驗證失敗: {exc}"


def validate_user_key(provider: str, api_key: str) -> tuple[bool, str]:
    """
    驗證用戶提供的 API key 是否有效

    Args:
        provider: LLM 提供商
        api_key: API key

    Returns:
        (是否有效, 錯誤訊息)
    """
    try:
        client = create_user_llm_client(provider, api_key)

        # 嘗試進行一個輕量的測試調用
        client.invoke([HumanMessage(content="Hi")])

        return True, "驗證成功"
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return False, explain_llm_exception(e)
