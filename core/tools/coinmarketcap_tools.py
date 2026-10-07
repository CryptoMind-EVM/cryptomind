"""
CoinMarketCap Tools — BYOK（使用者自帶 CMC Pro API 金鑰）

CMC 行情/市值資料品質高但需付費金鑰，故採強制 BYOK：
使用者未在「工具設定」填入自己的 CMC 金鑰時，工具回傳提示訊息。

使用既有 httpx，不新增套件依賴。
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict

import httpx
from langchain_core.tools import tool

from api.utils import logger

_CMC_QUOTES_ENDPOINT = (
    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
)


def _missing_key_response() -> Dict:
    return {
        "error": "This feature requires your own CoinMarketCap API key.",
        "hint": "Add your CoinMarketCap API key in Tool Settings (apply: https://pro.coinmarketcap.com/account)",
        "alternative": "You can also use the free get_crypto_price / get_crypto_market_cap (CoinGecko).",
    }


def fetch_cmc_quote(symbol: str, api_key: str, convert: str = "USD") -> Dict:
    """查詢 CMC 最新行情（單一幣種）。"""
    sym = symbol.strip().upper()
    logger.info(f"🔎 CoinMarketCap quote for: {sym}")
    try:
        resp = httpx.get(
            _CMC_QUOTES_ENDPOINT,
            params={"symbol": sym, "convert": convert},
            headers={"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", {}).get(sym)
        if not data:
            return {"error": f"CoinMarketCap: no data found for {sym}"}
        quote = data.get("quote", {}).get(convert, {})
        price = quote.get("price")
        cmc_rank = data.get("cmc_rank")

        # C-3 修復：price sanity + rank 可疑標記。
        # price <= 0 / None 不該當真實價回傳（會被 LLM 當有效值做投資判斷）。
        if not isinstance(price, (int, float)) or price <= 0:
            logger.warning(
                "[CMC] %s price=%r (<=0/None) — refusing to return junk price", sym, price
            )
            return {"error": f"Invalid price data for {sym} (data source returned {price}); please try again later."}

        result = {
            "symbol": sym,
            "name": data.get("name"),
            "price": price,
            "market_cap": quote.get("market_cap"),
            "volume_24h": quote.get("volume_24h"),
            "percent_change_24h": quote.get("percent_change_24h"),
            "percent_change_7d": quote.get("percent_change_7d"),
            "cmc_rank": cmc_rank,
            "convert": convert,
            "source": "CoinMarketCap",
        }

        # 主流幣（top 20）若 rank 異常高，標記可疑（極可能是同名仿冒幣或 BYOK
        # 金鑰 scope 問題）。LLM 看到 data_suspicious=true 應提醒用戶資料待確認。
        # 真實 BTC rank=1、ETH rank=2；若 BTC 回 rank=4500 明顯是仿冒幣。
        _MAJOR_COINS = {
            "BTC": 1, "ETH": 2, "USDT": 3, "BNB": 4, "SOL": 5,
            "USDC": 6, "XRP": 7, "DOGE": 8, "TON": 9, "ADA": 10,
        }
        if sym in _MAJOR_COINS and isinstance(cmc_rank, int) and cmc_rank > 50:
            result["data_suspicious"] = True
            result["suspicion_reason"] = (
                f"{sym} is expected to have rank ≤ {_MAJOR_COINS[sym]} but returned "
                f"rank={cmc_rank}; this may be a same-name impersonation token or an API key issue, "
                f"please verify the data."
            )
            logger.warning("[CMC] %s rank=%s (expected %s) — marked suspicious",
                           sym, cmc_rank, _MAJOR_COINS[sym])

        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (401, 403):
            return {"error": "The CoinMarketCap API key is invalid or lacks permission; please check your API key."}
        logger.error(f"❌ CMC quote HTTP {status} for {sym}")
        return {"error": f"CoinMarketCap query failed (HTTP {status})"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"❌ CMC quote failed: {e}")
        return {"error": f"CoinMarketCap query failed: {e}"}


@tool
def get_cmc_quote(symbol: str, convert: str = "USD") -> Dict:
    """使用 CoinMarketCap 查詢加密貨幣即時行情（價格、市值、漲跌幅、排名）。

    需使用者自帶 CoinMarketCap Pro API 金鑰（BYOK）。

    Args:
        symbol: 幣種代號（如 BTC、ETH、PI）。
        convert: 報價幣別，預設 USD。
    """
    from core.tools.key_resolver import resolve_tool_key

    api_key = resolve_tool_key("coinmarketcap", official_env=None)
    if not api_key:
        return _missing_key_response()
    return fetch_cmc_quote(symbol, api_key, convert=convert)
