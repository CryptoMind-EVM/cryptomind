"""
Market API Helper Functions
"""

import asyncio
from datetime import datetime, timezone

import numpy as np

from analysis.crypto_screener_light import (
    screen_top_cryptos_light as screen_top_cryptos,
)
from analysis.market_pulse import get_market_pulse
from api.globals import (
    FUNDING_RATE_CACHE,
    MARKET_PULSE_CACHE,
    cached_screener_result,
)
from api.symbols import (
    normalize_base_symbol,
    sanitize_base_symbols,
    sanitize_pair_symbols,
)
from api.utils import logger, run_sync

# In-memory cache for static symbol lists
SYMBOL_CACHE = {"okx": {"data": None, "timestamp": 0}}


def normalize_funding_symbol(symbol: str) -> str:
    """Normalize funding rate symbol by removing suffixes."""
    return normalize_base_symbol(symbol)


def filter_funding_data_by_symbols(data: dict, symbol_list: list) -> dict:
    """Filter funding rate data for specific symbols."""
    filtered_data = {}
    for sym in symbol_list:
        normalized_sym = normalize_funding_symbol(sym)
        if normalized_sym in data:
            filtered_data[normalized_sym] = data[normalized_sym]
    return filtered_data


def parse_symbols_param(symbols: str) -> list:
    """Parse comma-separated symbols parameter."""
    return sanitize_base_symbols(symbols.split(","))


def compute_top_bottom_rates(rates: list, limit: int) -> tuple:
    """Compute top bullish and bearish rates from sorted list."""
    top_bullish = rates[:limit]
    top_bearish = rates[-limit:][::-1]
    return top_bullish, top_bearish


def sort_funding_rates(data: dict) -> list:
    """Sort funding rates by rate value (descending)."""
    return sorted(
        [(sym, info.get("fundingRate", 0)) for sym, info in data.items()],
        key=lambda x: x[1],
        reverse=True,
    )


def format_funding_rates_response(
    timestamp: str,
    total_count: int,
    top_bullish: list,
    top_bearish: list,
    filtered_data: dict = None,
    filtered_count: int = None,
    source: str = None,
) -> dict:
    """Format funding rates API response."""
    response = {
        "timestamp": timestamp,
        "total_count": total_count,
        "source": source,
        "top_bullish": [{"symbol": s, "fundingRate": r} for s, r in top_bullish],
        "top_bearish": [{"symbol": s, "fundingRate": r} for s, r in top_bearish],
    }

    if filtered_data is not None:
        response["data"] = filtered_data
        response["filtered_count"] = filtered_count
    else:
        response["data"] = {
            sym: FUNDING_RATE_CACHE.get("data", {}).get(sym)
            for sym, _ in top_bullish + top_bearish
        }

    return response


def normalize_market_symbol(symbol: str) -> str:
    """Normalize market pulse symbol by removing suffixes."""
    return normalize_base_symbol(symbol)


def try_get_cached_pulse(symbol: str, deep_analysis: bool) -> dict:
    """Try to get market pulse from cache (returns None if miss).

    Bug 3 修正（2026-08-21 DANNY 回報 Pulse 資料過期）：
    DB 載入的舊資料沒有過期檢查——timestamp 可以是一個月前。
    加入 1 小時過期門檻：超過就視為 cache miss，觸發重新分析。
    """
    if not deep_analysis and symbol in MARKET_PULSE_CACHE:
        cached_data = MARKET_PULSE_CACHE[symbol].copy()
        # 過期檢查：timestamp 超過 1 小時 → 不用，重新分析
        ts = cached_data.get("timestamp")
        if ts:
            try:
                from datetime import datetime, timedelta, timezone  # noqa: PLC0415

                parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - parsed > timedelta(hours=1):
                    return None  # stale — cache miss
            except (ValueError, TypeError):
                pass  # 無法解析就不擋（serve as-is）
        cached_data["source_mode"] = "public_cache"
        return cached_data
    return None


async def perform_deep_analysis(
    symbol: str, sources: str, llm_key: str, llm_provider: str
) -> dict:
    """Perform deep analysis using user's LLM key."""
    from analysis.market_pulse import MarketPulseAnalyzer
    from api.services import save_market_pulse_cache
    from utils.llm_client import create_llm_client_from_config

    logger.info(f"Deep Analysis Mode: Using User Key for {symbol}")
    user_client, _ = create_llm_client_from_config(
        {"provider": llm_provider, "api_key": llm_key}
    )

    analyzer = MarketPulseAnalyzer(client=user_client)
    enabled_sources = sources.split(",") if sources else None

    result = await run_sync(
        lambda: analyzer.analyze_movement(symbol, enabled_sources=enabled_sources)
    )

    if result and "error" not in result:
        result["source_mode"] = "deep_analysis"
        result["analyzed_by"] = llm_provider
        MARKET_PULSE_CACHE[symbol] = result
        await run_sync(save_market_pulse_cache)

    return result


# 平台免費模型的深度分析全站共用（core/platform_share.py）：1 小時內有人用平台模型
# 分析過同一檔就直接給那份（輸入只有公開行情＋新聞）；要求重新整理也至少隔 15 分鐘才重算。
SHARED_DEEP_MAX_AGE_S = 3600
SHARED_DEEP_REFRESH_COOLDOWN_S = 900


def shared_platform_deep_report(symbol: str, refresh: bool = False) -> dict | None:
    """MARKET_PULSE_CACHE 裡平台模型產生、還新鮮的深度分析；沒有回 None。"""
    from core.platform_share import is_platform_model

    entry = MARKET_PULSE_CACHE.get(symbol)
    if not entry or entry.get("source_mode") != "deep_analysis":
        return None
    if not is_platform_model(entry.get("analyzed_by")):
        return None
    try:
        ts = datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > SHARED_DEEP_MAX_AGE_S or (
        refresh and age > SHARED_DEEP_REFRESH_COOLDOWN_S
    ):
        return None
    return {**entry, "shared": True}


async def deep_analysis_shared_or_run(
    symbol: str, sources: str, credentials: dict, refresh: bool = False
) -> dict:
    """平台模型：先給共用的那份，沒有才算（同一檔同時多人觸發只算一次）；自帶金鑰照舊。"""
    from core.platform_share import is_platform_model, lock_for

    provider = credentials["provider"]
    if not is_platform_model(provider):
        return await perform_deep_analysis(
            symbol, sources, credentials["api_key"], provider
        )
    async with lock_for(f"pulse:{symbol}"):
        shared = shared_platform_deep_report(symbol, refresh)
        if shared:
            return shared
        return await perform_deep_analysis(
            symbol, sources, credentials["api_key"], provider
        )


async def perform_on_demand_analysis(
    symbol: str, sources: str, semaphore: asyncio.Semaphore
) -> dict:
    """Perform on-demand market pulse analysis."""
    enabled_sources = sources.split(",") if sources else None
    from api.services import save_market_pulse_cache

    async with semaphore:
        result = await run_sync(
            lambda: get_market_pulse(symbol, enabled_sources=enabled_sources)
        )

    if result and "error" not in result:
        result["source_mode"] = "on_demand"
        MARKET_PULSE_CACHE[symbol] = result
        asyncio.create_task(asyncio.to_thread(save_market_pulse_cache))
        return result

    return None


def create_pending_pulse_response(symbol: str) -> dict:
    """Create a pending response for market pulse analysis."""
    return {
        "symbol": symbol,
        "status": "pending",
        "source_mode": "awaiting_update",
        "message": "Analysis in progress, please try again",
        "current_price": 0,
        "change_24h": 0,
        "change_1h": 0,
        "report": {
            "summary": "System is generating initial report for this symbol.",
            "key_points": [],
            "highlights": [],
            "risks": [],
        },
    }


def replace_nan_in_dataframe(df):
    """Replace NaN values with None in dataframe."""
    if df.empty:
        return df
    return df.replace({np.nan: None})


def format_screener_response(df_gainers, df_losers, df_volume) -> dict:
    """Format screener results as API response."""
    top_performers = replace_nan_in_dataframe(df_gainers)
    top_losers = replace_nan_in_dataframe(df_losers)
    top_volume = replace_nan_in_dataframe(df_volume)

    return {
        "top_gainers": top_performers.to_dict(orient="records"),
        "top_losers": top_losers.to_dict(orient="records"),
        "top_volume": top_volume.to_dict(orient="records"),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def try_get_cached_screener(refresh: bool):
    """Try to get cached screener result."""
    if not refresh and cached_screener_result["data"] is not None:
        return cached_screener_result["data"]
    return None


async def run_custom_screener(request, market_pulse_cache, trigger_analysis_func):
    """Run custom screener for specific symbols."""
    sanitized_symbols = sanitize_pair_symbols(request.symbols or [])
    if not sanitized_symbols:
        logger.warning(
            "Custom screener request contains no valid symbols; falling back to default screener."
        )
        return await run_default_screener(request.exchange, market_pulse_cache)

    logger.info(
        f"Running custom screener: {request.exchange}, Symbols: {len(sanitized_symbols)}"
    )

    asyncio.get_running_loop().create_task(trigger_analysis_func(sanitized_symbols))

    summary_df, top_performers, oversold, overbought = await run_sync(
        lambda: screen_top_cryptos(
            exchange=request.exchange,
            limit=len(sanitized_symbols),
            interval="1d",
            target_symbols=sanitized_symbols,
            market_pulse_data=market_pulse_cache,
        )
    )

    return {
        "top_gainers": replace_nan_in_dataframe(top_performers).to_dict(
            orient="records"
        ),
        "top_losers": replace_nan_in_dataframe(oversold).to_dict(orient="records"),
        "top_volume": replace_nan_in_dataframe(summary_df).to_dict(orient="records"),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


async def run_default_screener(exchange: str, market_pulse_cache):
    """Run default screener for top 10 cryptocurrencies."""
    df_volume, df_gainers, df_losers, _ = await run_sync(
        lambda: screen_top_cryptos(
            exchange=exchange,
            limit=10,
            interval="1d",
            target_symbols=None,
            market_pulse_data=market_pulse_cache,
        )
    )

    result_data = format_screener_response(df_gainers, df_losers, df_volume)

    timestamp_str = datetime.now(timezone.utc).isoformat()
    cached_screener_result["timestamp"] = timestamp_str
    cached_screener_result["data"] = result_data

    logger.info("Manual screener refresh complete (RAM updated).")
    return result_data
