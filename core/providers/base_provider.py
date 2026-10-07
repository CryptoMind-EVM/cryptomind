"""
base_provider.py — 統一市場資料 Provider 抽象層

設計原則：
1. 介面全 sync（yfinance 本身就是 sync）；Router/Tool 需要 async 時自行用
   asyncio.to_thread 包裝。避免 nest_asyncio 混用。
2. Provider 內部負責 symbol 後綴正規化（".TW"/".HK" 等），呼叫者只需傳原始代碼。
3. 內建 TTL cache（in-memory），避免重複網路 IO。
4. 用 factory 取得 singleton：get_provider("tw") 永遠回同一個實例。
"""

from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from core import shared_cache

# ── 支援的市場代碼 ────────────────────────────────────────────────────────────
MarketCode = Literal["us", "tw", "hk", "jp", "kr", "in", "cn"]
SUPPORTED_MARKETS: tuple[str, ...] = ("us", "tw", "hk", "jp", "kr", "in", "cn")


# ── 後綴規則 ──────────────────────────────────────────────────────────────────
# 各市場 yfinance 後綴對應。US 無後綴，A 股要依代碼前綴決定 .SS 或 .SZ。
_SUFFIX_MAP: Dict[str, str] = {
    "us": "",
    "tw": ".TW",
    "hk": ".HK",
    "jp": ".T",
    "kr": ".KS",
    "in": ".NS",
    # cn 用 _normalize_cn_symbol 動態決定
}


def normalize_symbol(symbol: str, market: str) -> str:
    """將原始代碼正規化為 yfinance 格式。

    Examples:
        normalize_symbol("2330", "tw")   → "2330.TW"
        normalize_symbol("0700", "hk")   → "0700.HK"
        normalize_symbol("700", "hk")    → "0700.HK"
        normalize_symbol("AAPL", "us")   → "AAPL"
        normalize_symbol("600519", "cn") → "600519.SS"
        normalize_symbol("000858", "cn") → "000858.SZ"
    """
    if not symbol:
        return symbol
    sym = symbol.strip().upper()

    if market == "hk":
        # yfinance 港股代號是 4 位補零：700／00700／700.HK → 0700.HK（5 位數如 80737 保留）
        code = sym.removesuffix(".HK")
        if code.isdecimal():
            return f"{int(code):04d}.HK"

    # 已有後綴 → 直接回傳
    if "." in sym and sym.split(".")[-1] in {
        "TW",
        "TWO",
        "HK",
        "T",
        "KS",
        "KQ",
        "NS",
        "BO",
        "SS",
        "SZ",
    }:
        return sym

    if market == "cn":
        # A 股代碼規則：6/9 開頭 → 上海(.SS)，否則 → 深圳(.SZ)
        if sym.isdigit() and len(sym) == 6:
            return f"{sym}.SS" if sym[0] in ("6", "9") else f"{sym}.SZ"
        return sym

    suffix = _SUFFIX_MAP.get(market, "")
    if suffix and sym.isdigit():
        return f"{sym}{suffix}"
    if suffix and not sym.endswith(suffix):
        # 英文代碼也可能需要後綴（如 RELIANCE → RELIANCE.NS）
        return f"{sym}{suffix}"
    return sym


# ── 抽象 Provider ────────────────────────────────────────────────────────────
class StockDataProvider(ABC):
    """統一市場資料 Provider 抽象介面。"""

    market: str = ""  # 子類必須設定

    # ── Cache（per-instance）──
    def __init__(self) -> None:
        self._cache: Dict[str, tuple[Any, float]] = {}
        self._cache_lock = threading.Lock()
        self._default_ttl = 300  # 5 分鐘

    # ── Subclass hooks ──
    @abstractmethod
    def get_price(self, symbol: str) -> Dict[str, Any]:
        """即時報價：price, change, changePercent, currency, name 等。"""
        raise NotImplementedError

    @abstractmethod
    def get_technicals(self, symbol: str) -> Dict[str, Any]:
        """技術指標：rsi, macd_histogram, ma20, ma50, 52w_high, 52w_low。"""
        raise NotImplementedError

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """基本面：pe_ratio, pb_ratio, beta, eps, dividend_yield, ..."""
        raise NotImplementedError

    def get_extras(self, symbol: str) -> Dict[str, Any]:
        """市場特有 extras。預設回傳空 dict；子類可 override。

        Examples:
            TWSEProvider.get_extras("2330") → {"institutional": {...}, "revenue": {...}}
            US Provider.get_extras("AAPL") → {"institutional_holders": [...]}
        """
        return {}

    def get_news(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        """新聞列表。預設回傳空 list；子類可 override。"""
        return []

    def get_klines(
        self, symbol: str, interval: str = "1d", limit: int = 200
    ) -> List[Dict[str, Any]]:
        """K 線 OHLCV。預設回傳空 list；子類可 override。"""
        return []

    # ── Snapshot（並行抓取完整數據）──
    def get_snapshot(self, symbol: str) -> Dict[str, Any]:
        """一次取得 price + technicals + fundamentals + extras + news。

        子類可 override 以提供並行版本（用 ThreadPoolExecutor）。
        """
        return {
            "symbol": symbol,
            "market": self.market,
            "price": self.get_price(symbol),
            "technicals": self.get_technicals(symbol),
            "fundamentals": self.get_fundamentals(symbol),
            "extras": self.get_extras(symbol),
            "news": self.get_news(symbol, limit=5),
        }

    # ── Symbol 正規化（子類可 override）──
    def normalize(self, symbol: str) -> str:
        """將原始代碼正規化為 yfinance 格式。"""
        return normalize_symbol(symbol, self.market)

    # ── Cache 工具 ──
    def _l2_key(self, key: str) -> str:
        """共用快取（Redis）的 namespaced key：md:{market}:{key}。"""
        market = getattr(self, "market", "") or "_"
        return f"md:{market}:{key}"

    def _l1_get(self, key: str) -> Optional[Any]:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                self._cache.pop(key, None)
                return None
            return value

    def _l1_set(self, key: str, value: Any, ttl: int) -> None:
        with self._cache_lock:
            self._cache[key] = (value, time.time() + ttl)

    def _cache_get(self, key: str) -> Optional[Any]:
        # L1：process 內快取（最快）
        value = self._l1_get(key)
        if value is not None:
            return value

        # L2：跨 process 共用快取（Redis）。命中後回填 L1。
        # Redis 不可用時 get_json 回 None，自動 fallback 到上游抓取。
        value = shared_cache.get_json(self._l2_key(key))
        if value is not None:
            self._l1_set(key, value, self._default_ttl)
            return value
        return None

    def _cache_set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        effective_ttl = ttl or self._default_ttl
        # 同時寫 L1 與 L2（L2 在鎖外，避免持鎖做網路 IO）
        self._l1_set(key, value, effective_ttl)
        shared_cache.set_json(self._l2_key(key), value, effective_ttl)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()


# ── Factory（lazy singleton per market）──────────────────────────────────────
_provider_instances: Dict[str, StockDataProvider] = {}
_factory_lock = threading.Lock()


def get_provider(market: str) -> StockDataProvider:
    """取得指定市場的 Provider（lazy singleton）。

    Args:
        market: "us"/"tw"/"hk"/"jp"/"kr"/"in"/"cn"

    Raises:
        ValueError: 不支援的市場代碼
    """
    market = market.lower()
    if market not in SUPPORTED_MARKETS:
        raise ValueError(
            f"Unsupported market: {market!r}. Must be one of {SUPPORTED_MARKETS}"
        )

    # 已快取 → 直接回
    cached = _provider_instances.get(market)
    if cached is not None:
        return cached

    # 雙重檢查鎖
    with _factory_lock:
        cached = _provider_instances.get(market)
        if cached is not None:
            return cached
        provider = _create_provider(market)
        _provider_instances[market] = provider
        return provider


def _create_provider(market: str) -> StockDataProvider:
    """建立指定市場的 Provider 實例（延遲 import 避免循環依賴）。"""
    if market == "tw":
        from .twse_provider import TWSEProvider

        return TWSEProvider()
    if market == "us":
        from .us_provider import USProvider

        return USProvider()
    # 其他市場（hk/jp/kr/in/cn）用通用 YahooFinanceProvider
    from .yahoo_provider import YahooFinanceProvider

    return YahooFinanceProvider(market=market)


def clear_provider_cache() -> None:
    """清空所有 Provider 實例（測試用）。"""
    with _factory_lock:
        for provider in _provider_instances.values():
            try:
                provider.clear_cache()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
        _provider_instances.clear()
