"""
global_stock_tools.py — LangChain Tools for Global Stock Markets

Covers: HK (港股), JP (日股), KR (韓股), IN (印股)
統一透過 core.providers.YahooFinanceProvider，與其他市場共用相同的資料流。

Symbol conventions:
  HK: append .HK   e.g. 0700.HK (Tencent)
  JP: append .T    e.g. 7203.T  (Toyota)
  KR: append .KS   e.g. 005930.KS (Samsung)
  IN: append .NS   e.g. RELIANCE.NS (Reliance)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict

from langchain.tools import tool

from core.tools.helpers import crypto_misroute_error, is_crypto_symbol

logger = logging.getLogger(__name__)

_MARKET_LABELS = {"hk": "Hong Kong", "jp": "Japan", "kr": "Korea", "in": "India"}


def _provider_for(market: str):
    """取得指定市場的 Provider（lazy singleton）。"""
    from core.providers import get_provider

    return get_provider(market.lower())


def _label(market: str) -> str:
    return _MARKET_LABELS.get(market.lower(), market.upper())


# ── Price ────────────────────────────────────────────────────────────────────


@tool("global_stock_price")
def global_stock_price(symbol: str, market: str = "hk") -> Dict:
    """查詢全球股市即時價格（港股/日股/韓股/印股）。
    market 參數：hk=港股, jp=日股, kr=韓股, in=印股
    symbol 可帶後綴（0700.HK）或純代號（0700）。
    """
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, _label(market))
    try:
        result = _provider_for(market).get_price(symbol)
        result["market"] = _label(market)
        return result
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── Technical Analysis ────────────────────────────────────────────────────────


@tool("global_stock_technical")
def global_stock_technical(symbol: str, market: str = "hk") -> Dict:
    """計算全球股市技術指標（港股/日股/韓股/印股）。
    返回 RSI(14), MACD histogram, MA20, MA50, 52週高低點。
    """
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, _label(market))
    try:
        provider = _provider_for(market)
        result = provider.get_technicals(symbol)
        if not result:
            return {"error": f"Unable to fetch technical indicators for {symbol}", "symbol": symbol}
        return {"symbol": provider.normalize(symbol), **result}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── Fundamentals ─────────────────────────────────────────────────────────────


@tool("global_stock_fundamentals")
def global_stock_fundamentals(symbol: str, market: str = "hk") -> Dict:
    """取得全球股市基本面（港股/日股/韓股/印股）。
    返回 PE/PB/Beta/股息率/EPS/營收成長/獲利成長/分析師目標/建議/產業。
    """
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, _label(market))
    try:
        provider = _provider_for(market)
        result = provider.get_fundamentals(symbol)
        if not result:
            return {"error": f"Unable to fetch fundamentals data for {symbol}", "symbol": symbol}
        return {"symbol": provider.normalize(symbol), **result}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── News ─────────────────────────────────────────────────────────────────────


@tool("global_stock_news")
def global_stock_news(symbol: str, market: str = "hk", limit: int = 5) -> list:
    """取得全球股市相關新聞（港股/日股/韓股/印股）。"""
    if is_crypto_symbol(symbol):
        return [crypto_misroute_error(symbol, _label(market))]
    try:
        return _provider_for(market).get_news(symbol, limit=limit)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[global_stock_news] {symbol} failed: {e}")
        return []


# ── Combined Snapshot ─────────────────────────────────────────────────────────


@tool("global_stock_snapshot")
def global_stock_snapshot(symbol: str, market: str = "hk") -> Dict:
    """一次取得全球股市完整快照（港股/日股/韓股/印股）。
    包含：即時價格 + 技術指標 + 基本面 + 近期新聞（5則）。
    比分別呼叫各工具更高效（內部用 ThreadPoolExecutor 並行）。
    market: hk=港股, jp=日股, kr=韓股, in=印股
    """
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, _label(market))
    try:
        provider = _provider_for(market)
        snap = provider.get_snapshot(symbol)
        snap["market"] = _label(market)
        return snap
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}
