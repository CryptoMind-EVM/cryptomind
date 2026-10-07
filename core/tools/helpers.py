"""
工具輔助函數
提供符號標準化、交易所查找等通用功能
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from core.config import SUPPORTED_EXCHANGES
from data.data_fetcher import get_data_fetcher

logger = logging.getLogger(__name__)


# 幣種改名對照（舊代號 → 交易所現行代號）。網路名稱不變（TON 鏈還是 TON），只有原生幣改名。
RENAMED_BASES = {"TON": "GRAM", "TONCOIN": "GRAM"}


def normalize_symbol(symbol: str, exchange: str = "okx") -> str:
    """
    標準化交易對符號

    Args:
        symbol: 原始符號，如 'BTC', 'BTC-USDT', 'BTCUSDT'
        exchange: 交易所名稱

    Returns:
        標準化後的符號，如 'BTC-USDT' (OKX) 或 'BTCUSDT' (Binance)
    """
    if not symbol:
        return ""
    symbol = symbol.upper().strip()

    # 1. 先提取基礎幣種 (Base Currency)
    base_symbol = symbol.replace("-", "").replace("_", "")

    if base_symbol.endswith("USDT"):
        base_symbol = base_symbol[:-4]
    elif base_symbol.endswith("BUSD"):
        base_symbol = base_symbol[:-4]
    elif base_symbol.endswith("USD"):
        base_symbol = base_symbol[:-3]

    # 1b. 改名過的幣：Toncoin 2026-06-15 改名 Gram（GRAM），交易所 TON 交易對已下架
    #     （OKX 6/16 刪 TON-USDT；Binance TONUSDT 停在 BREAK、價格凍在 $1.60）。
    #     使用者仍會打「TON」，對交易所要換成 GRAM，否則拿到的是凍結的舊價。
    base_symbol = RENAMED_BASES.get(base_symbol, base_symbol)

    # 2. 根據交易所格式化
    if exchange.lower() == "binance":
        return f"{base_symbol}USDT"
    else:  # okx (default)
        return f"{base_symbol}-USDT"


def find_available_exchange(symbol: str) -> Tuple[Optional[str], Optional[str]]:
    """
    查找交易對可用的交易所

    Args:
        symbol: 加密貨幣符號

    Returns:
        (exchange, normalized_symbol) 或 (None, None)
    """
    for exchange in SUPPORTED_EXCHANGES:
        try:
            normalized = normalize_symbol(symbol, exchange)
            fetcher = get_data_fetcher(exchange)
            test_data = fetcher.get_historical_klines(normalized, "1d", limit=1)
            if test_data is not None and not test_data.empty:
                return (exchange, normalized)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            continue
    return (None, None)


# 常用加密貨幣符號列表（從 data/crypto_symbols.json 載入，單一真相來源）。
# 取代原本寫死在 code 裡的 73 個符號清單。新增幣種改 JSON 即可，不用改 code。
_CRYPTO_SYMBOLS_CACHE: Optional[List[str]] = None


def _load_crypto_symbols() -> List[str]:
    """從 data/crypto_symbols.json 載入加密貨幣符號清單（lazy cache）。

    graceful：載入失敗退回內建最小清單（BTC/ETH/SOL），確保工具不 crash。
    """
    global _CRYPTO_SYMBOLS_CACHE
    if _CRYPTO_SYMBOLS_CACHE is not None:
        return _CRYPTO_SYMBOLS_CACHE
    fallback = ["BTC", "ETH", "SOL"]
    try:
        path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "crypto_symbols.json"
        )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", []) if isinstance(data, dict) else []
        if symbols:
            _CRYPTO_SYMBOLS_CACHE = symbols
            return symbols
        logger.warning("[helpers] crypto_symbols.json is empty, falling back")
    except Exception as e:
        logger.warning(
            f"[helpers] failed to load crypto_symbols.json ({e}), falling back"
        )
    _CRYPTO_SYMBOLS_CACHE = fallback
    return _CRYPTO_SYMBOLS_CACHE


# 向後相容：CRYPTO_SYMBOLS 保留為屬性，取值時動態載入（避免 import 時讀檔）。
# 既有 `from core.tools.helpers import CRYPTO_SYMBOLS` 仍可用，但建議改呼叫
# _load_crypto_symbols() 以確保拿到最新清單。
class _CryptoSymbolsProxy:
    """lazy proxy：存取 CRYPTO_SYMBOLS 時才載入 JSON（避免 import side-effect）。"""

    def __iter__(self):
        return iter(_load_crypto_symbols())

    def __len__(self):
        return len(_load_crypto_symbols())

    def __contains__(self, item):
        return item in _load_crypto_symbols()

    def __getitem__(self, index):
        return _load_crypto_symbols()[index]


CRYPTO_SYMBOLS = _CryptoSymbolsProxy()


def is_crypto_symbol(symbol: str) -> bool:
    """判斷 symbol 是否為加密貨幣代號。

    單一真相來源：data/crypto_symbols.json（73 個主流幣 + memo/長尾）。
    股票工具入口用此 guard 攔截 LLM 誤把 BTC/ETH 丟進來查美股/台股，
    避免 yfinance 把 BTC 解析成 Grayscale ETF（導致回傳 $0.00001047 的垃圾價）。

    比對規則：取符號 base（去除 yfinance 後綴如 -USD/=F/.TW）後全大寫精確比對。
    """
    if not symbol or not isinstance(symbol, str):
        return False
    base = re.split(r"[-=.]|/US", symbol.strip(), maxsplit=1)[0].strip().upper()
    if not base:
        return False
    return base in set(_load_crypto_symbols())


def crypto_misroute_error(symbol: str, intended_tool: str = "US stocks") -> dict:
    """股票工具收到 crypto symbol 時的統一錯誤回應（附正確工具提示）。

    Args:
        symbol: 被誤丟進來的 crypto 代號（如 BTC）。
        intended_tool: 呼叫端的市場名稱（美股/台股/港股...），用於錯誤訊息。
    """
    base = re.split(r"[-=.]|/US", symbol.strip(), maxsplit=1)[0].strip().upper()
    return {
        "error": f"{base} is a cryptocurrency and cannot be queried with the {intended_tool} tool.",
        "hint": f"Use get_crypto_price instead to fetch the live crypto price for {base}.",
        "symbol": symbol,
    }


# 常見非幣種詞（避免誤識別）
COMMON_WORDS = {
    "USDT",
    "BUSD",
    "USD",
    "THE",
    "AND",
    "FOR",
    "ARE",
    "CAN",
    "SEE",
    "DID",
    "HAS",
    "WAS",
    "NOT",
    "BUT",
    "ALL",
    "ANY",
    "NEW",
    "NOW",
    "ONE",
    "TWO",
    "BUY",
    "SELL",
    "PAY",
    "GET",
    "RUN",
    "SET",
    "TOP",
    "LOW",
    "KEY",
    "USE",
    "TRY",
    "BIG",
    "OLD",
    "BAD",
    "HOT",
    "RED",
    "BIT",
    "EAT",
    "FLY",
    "MAN",
    "BOY",
    "ART",
    "CAR",
    "DAY",
    "WAY",
    "HEY",
    "WHY",
    "HOW",
    "WHO",
}


def extract_crypto_symbols(text: str) -> List[str]:
    """
    從文本中提取加密貨幣符號

    Args:
        text: 用戶輸入文本

    Returns:
        識別到的加密貨幣符號列表
    """
    # 使用負向前瞻和負向後顧來匹配幣種符號前後不是字母數字
    crypto_symbols = _load_crypto_symbols()
    escaped_symbols = [re.escape(symbol) for symbol in crypto_symbols]
    pattern = r"(?<![a-zA-Z0-9])(" + "|".join(escaped_symbols) + r")(?![a-zA-Z0-9])"
    matches = re.findall(pattern, text.upper(), re.IGNORECASE)

    # 去重並過濾常見非幣種詞
    return list(set(m for m in matches if m not in COMMON_WORDS))


def format_price(price: float) -> str:
    """
    智能價格格式化函數

    根據價格大小自動選擇適當的小數位數，
    確保低價代幣（如 SHIB、PEPE）能正確顯示。

    Args:
        price: 價格數值

    Returns:
        格式化後的價格字符串
    """
    if price is None or price == 0:
        return "N/A"

    abs_price = abs(price)

    # 根據價格範圍選擇小數位數
    if abs_price >= 1000:
        # 高價幣（如 BTC）：整數 + 2 位小數
        return f"${price:,.2f}"
    elif abs_price >= 1:
        # 中價幣（如 ETH, SOL）：2 位小數
        return f"${price:.2f}"
    elif abs_price >= 0.01:
        # 低價幣（如 DOGE）：4 位小數
        return f"${price:.4f}"
    elif abs_price >= 0.0001:
        # 超低價幣（如 SHIB）：6 位小數
        return f"${price:.6f}"
    else:
        # 極低價幣（如 PEPE）：8 位小數
        return f"${price:.8f}"
