"""
LLM Client Factory - Unified LangChain Implementation
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict

from dotenv import load_dotenv

# LangChain Imports
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from core.model_config import (
    GEMINI_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    OPENAI_LEGACY_MODEL,
    get_provider_runtime,
    resolve_server_api_key,
)

# Import settings
from utils.settings import Settings

# Configure Logger
try:
    from api.utils import logger
except ImportError:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

load_dotenv()


class LLMClientFactory:
    """
    Factory for creating unified LangChain LLM clients.
    Supports OpenAI, Google Gemini, OpenRouter, and Local models via LangChain.
    """

    @staticmethod
    def _get_api_key(provider: str) -> str:
        """
        依 provider 取得 server 端 API Key。

        先查 Settings（向後相容既有設定），再 fallback 到 PROVIDER_REGISTRY
        宣告的環境變數。新增 provider 不必再改這裡——只要在註冊表填 api_key_envs。
        """
        # 既有 Settings 欄位優先（向後相容）
        settings_attr = {
            "openai": "OPENAI_API_KEY",
            "openai_server": "SERVER_OPENAI_API_KEY",
            "google_gemini": "GOOGLE_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }.get(provider)
        if settings_attr:
            value = getattr(Settings, settings_attr, "")
            if value:
                return value

        # 通用：依註冊表的 api_key_envs 從環境變數解析
        return resolve_server_api_key(provider)

    @staticmethod
    def create_client(
        provider: str, model: str = None, api_key: str = None
    ) -> BaseChatModel:
        """
        Create a LangChain ChatModel instance.

        Args:
            provider: Provider name ("openai", "openrouter", "google_gemini", "local")
            model: Model name (e.g., "gpt-4", "gemini-pro")
            api_key: User-provided API key (overrides env vars when set)

        Returns:
            BaseChatModel: A configured LangChain chat model.
        """
        # 優先使用傳入的 user key，否則 fallback 到環境變數
        api_key = api_key or LLMClientFactory._get_api_key(provider)

        # Map internal provider names to LangChain init_chat_model providers
        lc_provider = "openai"  # Default to openai (works for openrouter/local too)
        kwargs = {}

        if provider == "local":
            # 本地模型（OpenAI 相容），不在註冊表內，單獨處理
            lc_provider = "openai"
            from core.config import LOCAL_LLM_CONFIG

            kwargs["base_url"] = LOCAL_LLM_CONFIG.get(
                "base_url", "http://localhost:8000/v1"
            )
            kwargs["api_key"] = LOCAL_LLM_CONFIG.get("api_key", "not-needed")
            kwargs["temperature"] = LOCAL_LLM_CONFIG.get("temperature", 0.1)
        else:
            # 路由來自 PROVIDER_REGISTRY（單一真實來源）
            runtime = get_provider_runtime(provider)
            if not runtime:
                raise ValueError(f"Unsupported provider: {provider}")
            lc_provider = runtime["lc_provider"]
            if not api_key and runtime.get("api_key_optional"):
                api_key = "local"  # 平台自架 llama-server 不驗 key
            if not api_key:
                raise ValueError(f"Missing API Key for provider '{provider}'.")
            kwargs["api_key"] = api_key
            if runtime["base_url"]:
                kwargs["base_url"] = runtime["base_url"]
            if runtime.get("request_extra_body"):
                kwargs["extra_body"] = dict(runtime["request_extra_body"])

        # Initialize the model
        try:
            # Default model names if not provided
            if not model:
                if provider == "google_gemini":
                    model = GEMINI_DEFAULT_MODEL
                else:
                    model = OPENAI_LEGACY_MODEL

            logger.info(f"Initializing LLM: Provider={lc_provider}, Model={model}")

            # Fix B（2026-07-20）：設定 max_retries + request_timeout。
            # 過去沒設 → LangChain 預設 max_retries=2 + 無 timeout，NEM 端點
            # ResourceExhausted 時可能 retry + backoff 累積數十秒。加上 timeout
            # 讓單一 LLM call 不超過 60s，避免 claw_loop 內部 LangGraph 反覆迭代
            # 卡到 4 分鐘（log 實測 _claw_loop_node 跑 259 秒）。
            #
            # max_retries 用 env 可覆寫（特殊情境如 openrouter 大模型可調高）。
            max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))
            request_timeout_sec = int(os.getenv("LLM_REQUEST_TIMEOUT_SEC", "60"))

            llm = init_chat_model(
                model=model,
                model_provider=lc_provider,
                temperature=0.5,  # Default temperature, can be overridden in invoke/bind
                max_retries=max_retries,
                request_timeout=request_timeout_sec,
                **kwargs,
            )

            return llm

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"Failed to initialize LLM client for {provider}/{model}: {e}")
            raise e

    @staticmethod
    def get_model_info(config: Dict[str, str]) -> str:
        provider = config.get("provider", "openai")
        model = config.get("model", "unknown")
        return f"{model} ({provider})"


def supports_json_mode(model: str) -> bool:
    """Check if model supports native JSON mode."""
    # This is less critical with LangChain as we can use parsers,
    # but still useful for deciding whether to set response_format={"type": "json_object"}
    # if we were using bind.
    unsupported_models = ["gemma", "llama"]
    model_lower = model.lower()
    for unsupported in unsupported_models:
        if unsupported in model_lower:
            return False
    return True


def extract_json_from_response(response_text: str) -> dict:
    """
    Extract JSON from response text.
    Handles ```json blocks, raw JSON, and text with extra context.
    """
    if not response_text or not response_text.strip():
        raise ValueError("Empty response")

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    # Try code blocks
    code_block_patterns = [
        r"```json\s*\n(.*?)\n```",
        r"```\s*\n(.*?)\n```",
    ]
    for pattern in code_block_patterns:
        matches = re.findall(pattern, response_text, re.DOTALL)
        if matches:
            try:
                return json.loads(matches[0])
            except json.JSONDecodeError:
                continue

    # Try finding { }
    first_brace = response_text.find("{")
    last_brace = response_text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        try:
            return json.loads(response_text[first_brace : last_brace + 1])
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass

    # Try dirtyjson if available
    try:
        import dirtyjson

        return dirtyjson.loads(response_text)
    except ImportError:
        pass
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        pass

    raise ValueError(f"Could not extract JSON from response: {response_text[:100]}...")


def create_llm_client_from_config(
    config: Dict[str, str],
    user_client: Any = None,
    user_provider: str = None,
    user_model: str = None,
) -> tuple:
    """
    Create LLM client from config or use provided user_client.

    Args:
        config: {"provider": "...", "model": "..."}
        user_client: Existing LangChain model instance (optional)

    Returns:
        (client, model_name)
    """
    model_from_config = config.get("model", OPENAI_DEFAULT_MODEL)

    # 1. Use user_client if provided
    if user_client:
        effective_model = user_model if user_model else model_from_config
        return user_client, effective_model

    # 2. Create new client
    provider_from_config = config.get("provider", "openai")
    effective_model = user_model if user_model else model_from_config

    api_key_from_config = config.get("api_key") or None

    logger.info(
        f"create_llm_client_from_config: Creating new client for {provider_from_config}/{effective_model} "
        f"({'User Key' if api_key_from_config else 'System Keys'})"
    )

    client = LLMClientFactory.create_client(
        provider_from_config, effective_model, api_key=api_key_from_config
    )
    return client, effective_model
