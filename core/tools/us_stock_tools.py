"""
US Stock Tools - LangChain Tools for US Stock Analysis

Provides LangChain @tool decorators for US stock data access.
Uses Yahoo Finance as the primary data source.

Available Tools:
- us_stock_price: Real-time price data
- us_technical_analysis: Technical indicators
- us_fundamentals: Fundamental data
- us_earnings: Earnings data and calendar
- us_news: Latest news
- us_institutional_holders: Institutional holdings
- us_insider_transactions: Insider trading data
"""

import asyncio
import os
from typing import Dict, List

from langchain.tools import tool

from core.tools.helpers import crypto_misroute_error, is_crypto_symbol

# 注意：us_data_provider 已停用（保留檔案供向後相容）。所有 US 工具改透過
# core.providers.USProvider 取得資料。
# from .us_data_provider import get_us_data_provider  # deprecated


def _us() -> "Any":  # noqa: F821
    """取得統一 USProvider（lazy singleton）。"""
    from core.providers import get_provider

    return get_provider("us")


@tool("us_stock_price")
def us_stock_price(symbol: str) -> Dict:
    """獲取美股即時價格數據（含漲跌幅、貨幣、來源等）。"""
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        return _us().get_price(symbol)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("us_technical_analysis")
def us_technical_analysis(symbol: str) -> Dict:
    """美股技術指標分析（RSI、MACD、MA20/50、52週高低點）。"""
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        result = _us().get_technicals(symbol)
        return result if result else {"error": "no technicals", "symbol": symbol}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("us_fundamentals")
def us_fundamentals(symbol: str) -> Dict:
    """美股基本面（PE/PB/Beta/EPS/股息率/分析師目標等）。"""
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        return _us().get_fundamentals(symbol)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("us_earnings")
def us_earnings(symbol: str) -> Dict:
    """美股財報數據（下次財報日期、歷史 EPS、預估 vs 實際）。"""
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        return _us().get_earnings(symbol)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("us_news")
def us_news(symbol: str, limit: int = 5) -> List[Dict]:
    """美股相關新聞。"""
    if is_crypto_symbol(symbol):
        return [crypto_misroute_error(symbol, "US stock")]
    try:
        return _us().get_news(symbol, limit=min(limit, 20))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return []


@tool("us_institutional_holders")
def us_institutional_holders(symbol: str) -> Dict:
    """美股機構持倉數據。"""
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        extras = _us().get_extras(symbol)
        inst = extras.get("institutional_holders") or {}
        return {"symbol": symbol, **inst}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("us_insider_transactions")
def us_insider_transactions(symbol: str) -> Dict:
    """美股內部人交易數據。"""
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        extras = _us().get_extras(symbol)
        insider = extras.get("insider_transactions") or {}
        return {"symbol": symbol, **insider}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("us_stock_snapshot")
def us_stock_snapshot(symbol: str) -> Dict:
    """一次取得美股完整快照（並行抓取所有數據）。
    包含：即時價格 + 技術指標 + 基本面 + 財報 + 機構持倉 + 內部人交易 + 新聞。
    """
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        return _us().get_snapshot(symbol)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ============ 工具註冊函數 ============


def register_us_stock_tools(tool_registry):
    """
    註冊所有美股工具到工具註冊表

    Args:
        tool_registry: ToolRegistry 實例
    """
    from core.agents.tool_registry import ToolMetadata

    tools = [
        ("us_stock_price", us_stock_price, "Get real-time US stock price data"),
        ("us_technical_analysis", us_technical_analysis, "US stock technical indicator analysis"),
        ("us_fundamentals", us_fundamentals, "US stock fundamentals data"),
        ("us_earnings", us_earnings, "US stock earnings data"),
        ("us_news", us_news, "US stock related news"),
        ("us_institutional_holders", us_institutional_holders, "US stock institutional holdings data"),
        ("us_insider_transactions", us_insider_transactions, "US stock insider transaction data"),
    ]

    for name, tool_func, desc in tools:
        # 創建 ToolMetadata
        metadata = ToolMetadata(
            name=name,
            description=desc,
            input_schema={"symbol": "str", "limit": "int (optional)"},
            handler=tool_func,
            allowed_agents=["us_stock", "crypto", "full_analysis", "manager"],
        )
        tool_registry.register(metadata)


# ============ 快速測試 ============

if __name__ == "__main__":
    sample_symbol = os.getenv("US_STOCK_SAMPLE_SYMBOL", "[SYMBOL]")

    print("Testing US stock tools...")
    print("=" * 70)

    # 測試價格
    print(f"\n[1] Testing us_stock_price ({sample_symbol})")
    result = us_stock_price.invoke({"symbol": sample_symbol})
    print(f"Price: ${result.get('price', 'N/A')}")
    print(
        f"Change: {result.get('change', 0):+.2f} ({result.get('change_percent', 0):+.2f}%)"
    )

    # 測試技術指標
    print(f"\n[2] Testing us_technical_analysis ({sample_symbol})")
    result = us_technical_analysis.invoke({"symbol": sample_symbol})
    print(f"RSI: {result.get('rsi', 'N/A')}")
    print(f"Overall signal: {result.get('summary', 'N/A')}")

    # 測試基本面
    print(f"\n[3] Testing us_fundamentals ({sample_symbol})")
    result = us_fundamentals.invoke({"symbol": sample_symbol})
    print(f"P/E: {result.get('pe_ratio', 'N/A')}")
    print(f"ROE: {result.get('roe', 'N/A')}")

    # 測試新聞
    print(f"\n[4] Testing us_news ({sample_symbol})")
    result = us_news.invoke({"symbol": sample_symbol, "limit": 3})
    print(f"Fetched {len(result)} news items")
    if result:
        print(f"Latest: {result[0].get('title', 'N/A')}")

    print("\n" + "=" * 70)
    print("Testing complete!")
