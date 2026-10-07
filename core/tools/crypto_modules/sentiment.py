"""
市場情緒工具
Fear & Greed Index, Trending Tokens, Futures Data, Current Time
"""

import asyncio
import os
import re
import time
from typing import Dict, Optional

import httpx
from langchain_core.tools import tool

_COINGECKO_CACHE: Dict = {}

# 資金費率 symbol 白名單：僅允許大寫英數（BTC / PEPE1000…）。
# 主機名一律寫死（binance/okx），symbol 只能進 query param——
# 此 regex 是縱深防禦，擋掉任何非預期字元。
_BASE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")


def _get_cached_coingecko_data(key: str, ttl_seconds: int = 300):
    if key in _COINGECKO_CACHE:
        timestamp, data = _COINGECKO_CACHE[key]
        if time.time() - timestamp < ttl_seconds:
            return data
    return None


def _set_cached_coingecko_data(key: str, data):
    _COINGECKO_CACHE[key] = (time.time(), data)


@tool
def get_fear_and_greed_index() -> str:
    """獲取加密貨幣市場全域的恐慌與貪婪指數 (Fear and Greed Index)"""
    try:
        resp = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data and "data" in data and len(data["data"]) > 0:
                current = data["data"][0]
                val = str(current.get("value")).strip()
                classification = str(current.get("value_classification")).strip()
                return f"## 🌡️ Global Crypto Market Fear & Greed Index\n\n- **Current Index**: {val} / 100\n- **Market Sentiment**: {classification}"
        return "Unable to fetch the Fear & Greed Index API right now."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Network error while fetching the Fear & Greed Index: {str(e)}"


@tool
def get_trending_tokens() -> str:
    """獲取目前全網最熱門搜尋的加密貨幣 (Trending Tokens)"""
    cache_key = "trending_tokens"
    cached_data = _get_cached_coingecko_data(cache_key, 300)
    if cached_data:
        return cached_data

    try:
        resp = httpx.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            coins = data.get("coins", [])
            if not coins:
                return "CoinGecko currently has no trending search data."

            result = "## 🔥 Top Trending Coins\n\n"
            for i, item in enumerate(coins[:7], 1):
                coin = item.get("item", {})
                symbol = coin.get("symbol", "").upper()
                name = coin.get("name", "")
                market_cap_rank = coin.get("market_cap_rank", "N/A")
                result += f"{i}. **{symbol}** ({name}) - Market Cap Rank: {market_cap_rank}\n"

            final_output = result + "\n*(Source: CoinGecko)*"
            _set_cached_coingecko_data(cache_key, final_output)
            return final_output
        return "Unable to connect to the CoinGecko API right now."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Network error while fetching trending tokens: {str(e)}"


def _funding_from_binance(base_symbol: str) -> Optional[dict]:
    """幣安 USDT-M 資金費率（主源）。失敗回 None 讓上層 fallback。"""
    try:
        resp = httpx.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": f"{base_symbol}USDT"},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        rate = data.get("lastFundingRate")
        if rate in (None, ""):
            return None
        return {
            "rate_pct": float(rate) * 100,
            "source": "Binance USDT-M Futures",
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def _funding_from_okx(base_symbol: str) -> Optional[dict]:
    """OKX 永續資金費率（備源，公開 API 免金鑰）。失敗回 None。

    主機名固定為環境變數 OKX_BASE_URL（預設官方 www.okx.com），
    symbol 僅作 query param 且已過 _BASE_SYMBOL_RE 白名單。
    """
    try:
        base_url = os.getenv("OKX_BASE_URL", "https://www.okx.com/api/v5").rstrip("/")
        resp = httpx.get(
            f"{base_url}/public/funding-rate",
            params={"instId": f"{base_symbol}-USDT-SWAP"},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if payload.get("code") != "0" or not payload.get("data"):
            return None
        rate = payload["data"][0].get("fundingRate")
        if rate in (None, ""):
            return None
        return {
            "rate_pct": float(rate) * 100,
            "source": "OKX Perpetual Swap",
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


@tool
def get_futures_data(symbol: str) -> str:
    """獲取加密貨幣永續合約的資金費率 (Funding Rate)"""
    try:
        base_symbol = (
            symbol.upper().replace("USDT", "").replace("BUSD", "").replace("-", "")
        )
        if not _BASE_SYMBOL_RE.match(base_symbol):
            return f"Invalid symbol: {symbol}"

        # 幣安為主、OKX 為備（幣安被 geo-block / 掛掉時工具不再直接失敗）。
        # 輸出動態標示實際來源，讓使用者知道資料來自哪個交易所。
        funding = _funding_from_binance(base_symbol)
        if funding is None:
            funding = _funding_from_okx(base_symbol)
        if funding is None:
            return (
                f"No funding rate data found for {symbol} "
                "(both Binance and OKX failed or have no such perpetual)."
            )

        funding_rate_pct = funding["rate_pct"]

        status = "Neutral"
        if funding_rate_pct > 0.01:
            status = "🌟 Extremely Bullish (longs crowded, watch for pullback risk)"
        elif funding_rate_pct > 0.005:
            status = "📈 Bullish (longs pay funding to shorts)"
        elif funding_rate_pct < -0.01:
            status = "🩸 Extremely Bearish (shorts crowded, watch for short-squeeze risk)"
        elif funding_rate_pct < 0:
            status = "📉 Bearish (shorts pay funding to longs)"

        return (
            f"## ⚖️ {base_symbol} Perpetual Futures Funding Rate\n\n"
            f"- **Current Funding Rate**: {funding_rate_pct:.4f}%\n"
            f"- **Market Long/Short Sentiment**: {status}\n\n"
            f"*(Source: {funding['source']})*"
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Network error while fetching the funding rate: {str(e)}"


@tool
def get_current_time_taipei() -> str:
    """獲取目前台灣/UTC+8的精準時間與日期"""
    from datetime import datetime

    import pytz

    tz = pytz.timezone("Asia/Taipei")
    now = datetime.now(tz)

    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    weekday_str = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ][now.weekday()]

    return f"🕰️ **Current System Time (UTC+8)**\nDate: {date_str} ({weekday_str})\nTime: {time_str}"
