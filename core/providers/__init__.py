"""
core.providers — 統一市場資料 Provider 層

提供統一的抽象介面 StockDataProvider，讓所有市場（US/TW/HK/JP/KR/IN/CN）
透過相同的方法存取資料：

    from core.providers import get_provider
    provider = get_provider("tw")
    price = provider.get_price("2330")
    extras = provider.get_extras("2330")

Provider 內部依市場選擇實作：
    - YahooFinanceProvider：所有市場的基礎（yfinance 通用）
    - TWSEProvider：台股延伸（補三大法人/月營收/PE/股利等 TWSE OpenAPI 資料）
"""

from .base_provider import (
    SUPPORTED_MARKETS,
    MarketCode,
    StockDataProvider,
    clear_provider_cache,
    get_provider,
)

__all__ = [
    "StockDataProvider",
    "MarketCode",
    "SUPPORTED_MARKETS",
    "get_provider",
    "clear_provider_cache",
]
