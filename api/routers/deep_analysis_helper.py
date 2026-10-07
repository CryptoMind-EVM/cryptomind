"""
Generic LLM deep analysis helper — shared by all pulse endpoints.

Provides:
  - deep_analyze_generic() — raw LLM call
  - get_deep_analysis()    — cache-aware wrapper (check Redis → call LLM → cache result)
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import Any

from api.utils import logger, run_sync

# 深度分析輸出語言對照。LLM prompt 以此語言名稱要求全文（含段落標題）輸出。
# key 對齊前端 LanguageSwitcher 的語系代碼（zh-TW / en / ru）。
_LANG_NAMES: dict[str, str] = {
    "zh-TW": "繁體中文",
    "zh-HK": "繁體中文",
    "zh": "繁體中文",
    "zh-CN": "简体中文",
    "en": "English",
    "en-US": "English",
    "ru": "Русский",
}
_DEFAULT_LANG = "zh-TW"


def _normalize_lang(language: str | None) -> str:
    """將傳入語系正規化成 _LANG_NAMES 的 key；無法辨識則回退預設。"""
    if not language:
        return _DEFAULT_LANG
    if language in _LANG_NAMES:
        return language
    # 寬鬆比對前綴（如 en-GB → en、ru-RU → ru）
    prefix = language.split("-")[0]
    for key in _LANG_NAMES:
        if key == prefix or key.split("-")[0] == prefix:
            return key
    return _DEFAULT_LANG


def _translate_llm_error(e: Exception) -> str:
    """將 LLM API 錯誤轉換成使用者可讀的繁體中文訊息。"""
    msg = str(e).lower()
    if any(k in msg for k in ("rate_limit", "rate limit", "429")):
        return "Request frequency exceeded, please wait a few seconds and try again"
    if any(k in msg for k in ("insufficient_quota", "insufficient credits", "402")):
        return "LLM API credits exhausted. Please top up on the provider site, or switch to another key in the settings page"
    if any(
        k in msg
        for k in ("invalid_api_key", "incorrect api key", "401", "unauthorized")
    ):
        return "Invalid LLM API key. Please re-check and save the correct key in the settings page"
    if any(k in msg for k in ("timeout", "timed out")):
        return "AI analysis timed out, please try again later"
    if any(k in msg for k in ("connection", "network", "unreachable")):
        return "Unable to connect to the AI service, please check your network"
    return "AI analysis is temporarily unavailable, please try again later"


async def deep_analyze_generic(
    symbol: str,
    context: str,
    llm_key: str,
    llm_provider: str,
    llm_model: str | None = None,
    language: str = _DEFAULT_LANG,
) -> tuple[str | None, str | None]:
    """
    Call the user's LLM with market context to generate an AI analysis summary.
    Returns (ai_text, error_message). On success error_message is None; on failure ai_text is None.
    ``language`` 控制報告輸出語言（zh-TW / en / ru）。
    """
    lang_name = _LANG_NAMES.get(_normalize_lang(language), _LANG_NAMES[_DEFAULT_LANG])
    logger.info(
        f"[deep_analyze_generic] {symbol} provider={llm_provider} model={llm_model} "
        f"lang={language} key={llm_key[:8]}..."
    )
    try:
        from langchain_core.messages import HumanMessage

        from utils.llm_client import create_llm_client_from_config

        config = {
            "provider": llm_provider,
            "api_key": llm_key,
        }
        if llm_model:
            config["model"] = llm_model

        client, _ = create_llm_client_from_config(config)

        prompt = (
            f"你是一位專業金融分析師。請根據以下市場數據，撰寫一份完整的市場脈動分析報告。\n"
            f"⚠️ 重要：全文（包含四個段落標題）必須使用「{lang_name}」撰寫，不得混用其他語言。\n"
            f"請依照以下四個段落結構輸出，每段 2-3 句，段落之間空一行（段落標題請翻譯成 {lang_name}）：\n"
            f"1. 【趨勢概覽】：描述當前價格位置、趨勢方向與 52 週高低點的相對位置\n"
            f"2. 【技術面信號】：解讀 RSI、MACD、均線等技術指標所顯示的動能與多空訊號\n"
            f"3. 【基本面 / 籌碼】：若有 P/E、市值、法人買賣超、外資持股等數據則納入分析；外匯或大宗商品則分析總體經濟背景\n"
            f"4. 【短線展望】：根據以上綜合說明短線情境（偏多／偏空／盤整的條件）與關鍵支撐壓力位（只當資料陳述，不是停損停利或進出場點），不要給買賣或進出場指令\n"
            f"請直接輸出分析內容，不要在段落標題外額外加說明；內容為資料分析，不構成投資建議，不要使用「建議買進／賣出」「停損／停利設在」「部位建議」等指令式用語。\n\n"
            f"市場數據：\n{context}"
        )

        response = await run_sync(lambda: client.invoke([HumanMessage(content=prompt)]))
        text = response.content if hasattr(response, "content") else str(response)
        # Sanitize: 移除 LLM 偶發的內部工具名稱洩漏（與 chat 主路徑用同一份 sanitizer）。
        # pulse 端點的 prompt 不含工具名，但 BYOK 模型仍可能幻覺出 get_crypto_price /
        # resolve_symbol 等名稱；統一在這裡兜底。
        # 為避免 claw_loop ↔ api/routers 循環 import，import 放函式內。
        from core.agents.manager.claw_loop import _clean_claw_response

        text = _clean_claw_response(text)
        return text.strip(), None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        user_msg = _translate_llm_error(e)
        logger.error(f"[deep_analyze_generic] {symbol} failed: {e}")
        return None, user_msg


async def get_deep_analysis(
    *,
    market: str,
    symbol: str,
    context: str,
    llm_key: str,
    llm_provider: str,
    llm_model: str | None = None,
    force_refresh: bool = False,
    user_id: str = "",
    language: str = _DEFAULT_LANG,
) -> dict[str, Any]:
    """
    Cache-aware deep analysis entry point for all pulse endpoints.

    Flow:
      1. If not force_refresh → check Redis cache → return cached if hit
      2. If force_refresh → check cooldown → block if cooling down
      3. Otherwise → call LLM → cache result → set cooldown

    Returns dict with keys:
      source_mode, ai_error, report, cached_at, cache_expires_at, cooldown_remaining

    平台免費模型的報告全站共用（owner＝core.platform_share.PLATFORM_OWNER），冷卻也共用；
    自帶金鑰的仍以 user_id 各自一份。
    """
    # 快取需按語言分流，否則 zh-TW 的報告會被當成 en/ru 回給其他語系使用者。
    # 不改動 cache 函式簽名，改以「symbol#語言」當快取 symbol 區隔。
    lang = _normalize_lang(language)
    cache_symbol = f"{symbol}#{lang}"
    from core.platform_share import cache_owner, is_platform_model, lock_for

    # 平台免費模型：輸入只有公開行情＋新聞，同一檔同一語言共用一份；同時多人觸發只算一次
    shared = is_platform_model(llm_provider)
    lock = lock_for(f"deep:{market}:{cache_symbol}") if shared else nullcontext()
    async with lock:
        return await _deep_analysis_for_owner(
            symbol,
            context,
            llm_key,
            llm_provider,
            llm_model,
            force_refresh,
            cache_owner(llm_provider, user_id),
            market,
            cache_symbol,
            lang,
        )


async def _deep_analysis_for_owner(
    symbol: str,
    context: str,
    llm_key: str,
    llm_provider: str,
    llm_model: str | None,
    force_refresh: bool,
    user_id: str,
    market: str,
    cache_symbol: str,
    lang: str,
) -> dict[str, Any]:
    """快取查詢／冷卻／呼叫 LLM／寫快取。``user_id`` 是快取擁有者（平台模型＝共用）。"""
    from core.ai_analysis_cache import (
        cache_analysis,
        check_cooldown,
        get_cached_analysis,
        set_cooldown,
    )

    result: dict[str, Any] = {
        "source_mode": "on_demand",
        "ai_error": None,
        "report": None,
        "cached_at": None,
        "cache_expires_at": None,
        "cooldown_remaining": None,
    }

    if not force_refresh:
        cached = await get_cached_analysis(market, cache_symbol, user_id)
        if cached:
            logger.info(
                "[get_deep_analysis] Cache hit %s:%s:%s", market, cache_symbol, user_id
            )
            return cached

    if force_refresh:
        remaining = await check_cooldown(market, cache_symbol, user_id)
        if remaining is not None:
            cached = await get_cached_analysis(market, cache_symbol, user_id)
            if cached:
                cached["cooldown_remaining"] = remaining
                logger.info(
                    "[get_deep_analysis] Cooldown active %s:%s:%s (%.0fs remaining)",
                    market,
                    cache_symbol,
                    user_id,
                    remaining,
                )
                return cached

    ai_text, ai_error = await deep_analyze_generic(
        symbol,
        context,
        llm_key,
        llm_provider,
        llm_model,
        language=lang,
    )

    if ai_text:
        result["source_mode"] = "deep_analysis"
        result["report"] = {"summary": ai_text, "key_points": []}
        await cache_analysis(market, cache_symbol, user_id, result)
        await set_cooldown(market, cache_symbol, user_id)
        # Re-read to get cached_at / cache_expires_at populated by cache_analysis
        cached = await get_cached_analysis(market, cache_symbol, user_id)
        if cached:
            result = cached
    elif ai_error:
        result["source_mode"] = "ai_error"
        result["ai_error"] = ai_error

    return result
