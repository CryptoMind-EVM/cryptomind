"""TON/USD 取價統一入口 — CoinGecko 主源 + TonAPI 備援。

為什麼需要 fallback 鏈:
  TON 幣的取價原本全專案只有 CoinGecko 單一來源(swap_limits._fetch_ton_usd_price),
  3 個呼叫點共用。CoinGecko free tier 有 429 rate limit,它一掛 swap 限額就
  fail-closed 拒絕放行、錢包估值歸零——碰錢路徑的單點依賴隱患。

設計(見 docs/plans/2026-08-10-ton-price-fallback-design.md):
  - 主源 CoinGecko(行為不變):聚合行情,免 key
  - 備援 TonAPI /v2/rates:indexer 級聚合現價(非 DEX 成交價),專案已重度
    依賴(jetton balances + safety),免 key 同步 httpx
  - 任一源成功 → 寫回 shared_cache;兩源都失敗 → None(fail-closed 不變)

為何不用 STON.fi/DeDust/Omnistor:它們是 DEX 成交價(含滑價/fee),語意與
「市場現價」不同,混進 fallback 會讓限額 USD 換算出現兩種語意。見設計文件方案選擇。
"""

from __future__ import annotations

import logging

import httpx

from core.config import TON_USD_PRICE_CACHE_TTL
from core.shared_cache import get_json, set_json

logger = logging.getLogger(__name__)

# 中性快取 key(取代舊的 "swap:ton_usd_price" — 取價不再只服務 swap)。
_PRICE_CACHE_KEY = "ton:usd_price"

# ── 主源:CoinGecko(free tier,免 key,有 429) ──
_TON_COINGECKO_ID = "the-open-network"
_COINGECKO_PRICE_URL = (
    f"https://api.coingecko.com/api/v3/simple/price?ids={_TON_COINGECKO_ID}&vs_currencies=usd"
)

# ── 備援:TonAPI(indexer 級聚合現價,免 key) ──
# 回傳結構:{"rates": {"TON": {"prices": {"USD": 1.33}, ...}}}
# Origin header 與既有 jetton_balances/ton_safety 同模式。
_TONAPI_RATES_URL = "https://tonapi.io/v2/rates?tokens=ton&currencies=usd"
_TONAPI_HEADERS = {"Origin": "https://tonapi.io"}


def get_ton_usd_price() -> float | None:
    """查 TON/USD 即時價,帶 shared_cache 快取。查不到回 None(fail-closed)。

    Fallback 順序:shared_cache → CoinGecko → TonAPI → None。
    純同步(httpx.get),呼叫端用 run_sync 包(與舊 _fetch_ton_usd_price 同模式)。
    """
    cached = get_json(_PRICE_CACHE_KEY)
    if cached and isinstance(cached, (int, float)) and cached > 0:
        return float(cached)

    price = _fetch_from_coingecko()
    if price is None:
        # CoinGecko 掛了(429/斷線/格式錯)→ 走 TonAPI 備援
        price = _fetch_from_tonapi()

    if price is not None and price > 0:
        set_json(_PRICE_CACHE_KEY, price, TON_USD_PRICE_CACHE_TTL)
        return price

    return None


def _fetch_from_coingecko() -> float | None:
    """主源:CoinGecko simple/price。失敗回 None(交上層走 fallback)。"""
    try:
        resp = httpx.get(_COINGECKO_PRICE_URL, timeout=10.0)
    except (httpx.HTTPError, OSError):
        logger.warning("[TonPrice] CoinGecko connection failed")
        return None

    if resp.status_code == 429:
        logger.warning("[TonPrice] CoinGecko 429 rate limit")
        return None
    if resp.status_code != 200:
        logger.warning("[TonPrice] CoinGecko HTTP %d", resp.status_code)
        return None

    try:
        body = resp.json()
        price = float(body.get(_TON_COINGECKO_ID, {}).get("usd", 0))
    except (ValueError, AttributeError, TypeError):
        logger.warning("[TonPrice] CoinGecko unexpected response format")
        return None

    return price if price > 0 else None


def _fetch_from_tonapi() -> float | None:
    """備援:TonAPI /v2/rates。CoinGecko 失敗時才打。

    回聚合現價(indexer 級,非單一 DEX 成交價),語意與 CoinGecko 一致,
    適合當限額 USD 換算的價源。
    """
    try:
        resp = httpx.get(_TONAPI_RATES_URL, headers=_TONAPI_HEADERS, timeout=10.0)
    except (httpx.HTTPError, OSError):
        logger.warning("[TonPrice] TonAPI connection failed")
        return None

    if resp.status_code != 200:
        logger.warning("[TonPrice] TonAPI HTTP %d", resp.status_code)
        return None

    try:
        # 結構:{"rates": {"TON": {"prices": {"USD": 1.33}}}}
        body = resp.json()
        price = float(body["rates"]["TON"]["prices"]["USD"])
    except (ValueError, KeyError, TypeError):
        logger.warning("[TonPrice] TonAPI unexpected response format")
        return None

    return price if price > 0 else None


__all__ = ["get_ton_usd_price"]
