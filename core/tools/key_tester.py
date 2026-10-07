"""
BYOK 工具金鑰測試器

每個 BYOK provider 一個最小測試呼叫 — 讓使用者儲存金鑰後能立即驗證是否有效，
不用等到實際使用工具時才發現金鑰錯了（特別是新聞工具失敗時會靜默返回 []）。

對外 API：
    test_provider_key(provider: str, api_key: str) -> dict

回傳格式：
    {
        "success": bool,        # 金鑰是否通過
        "message": str,         # 人類可讀的詳情（含失敗原因）
        "status_code": int,     # provider 回的 HTTP status（若適用）
        "latency_ms": int,      # 測試耗時
    }
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # 每個測試呼叫最多等 10 秒


def _ok(message: str, status_code: int, latency_ms: int) -> Dict:
    return {"success": True, "message": message, "status_code": status_code, "latency_ms": latency_ms}


def _fail(message: str, status_code: int, latency_ms: int) -> Dict:
    return {"success": False, "message": message, "status_code": status_code, "latency_ms": latency_ms}


def _test_cryptopanic(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.get(
            "https://cryptopanic.com/api/developer/v2/posts/",
            params={"auth_token": api_key, "public": "true", "limit": 1},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code == 401:
            return _fail("CryptoPanic rejected the key (401 Unauthorized)", 401, latency)
        if r.status_code != 200:
            return _fail(f"CryptoPanic returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        if isinstance(data, dict) and "results" in data:
            return _ok("CryptoPanic key is valid", 200, latency)
        return _fail("CryptoPanic returned an unexpected format (missing results field)", 200, latency)
    except requests.Timeout:
        return _fail("CryptoPanic did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"CryptoPanic test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_newsapi(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"country": "us", "pageSize": 1, "apiKey": api_key},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code == 401:
            return _fail("NewsAPI rejected the key (401 Unauthorized)", 401, latency)
        if r.status_code == 429:
            return _fail("NewsAPI key is valid but rate limit reached (429) — please retry later", 429, latency)
        if r.status_code != 200:
            return _fail(f"NewsAPI returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        if data.get("status") == "ok":
            return _ok("NewsAPI key is valid", 200, latency)
        return _fail(f"NewsAPI response status={data.get('status')}", 200, latency)
    except requests.Timeout:
        return _fail("NewsAPI did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"NewsAPI test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_tavily(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": "test", "max_results": 1},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code == 401:
            return _fail("Tavily rejected the key (401 Unauthorized)", 401, latency)
        if r.status_code != 200:
            return _fail(f"Tavily returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        if "results" in data:
            return _ok("Tavily key is valid", 200, latency)
        return _fail("Tavily returned an unexpected format (missing results)", 200, latency)
    except requests.Timeout:
        return _fail("Tavily did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"Tavily test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_serper(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": "test", "num": 1},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        # Serper 對壞金鑰回 403（不是 401）：{"message":"Unauthorized.","statusCode":403}
        if r.status_code in (401, 403):
            return _fail("Serper rejected the key (Unauthorized)", r.status_code, latency)
        if r.status_code == 429:
            return _fail(
                "Serper key is valid but the monthly quota is exhausted (429)", 429, latency
            )
        if r.status_code != 200:
            return _fail(f"Serper returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        if "organic" in data:
            return _ok("Serper key is valid", 200, latency)
        return _fail("Serper returned an unexpected format (missing organic)", 200, latency)
    except requests.Timeout:
        return _fail("Serper did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"Serper test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_fmp(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/search",
            params={"query": "AAPL", "limit": 1, "apikey": api_key},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code in (401, 403):
            return _fail("FMP rejected the key (Invalid API KEY)", r.status_code, latency)
        if r.status_code == 429:
            return _fail("FMP key is valid but the daily quota is exhausted (429)", 429, latency)
        if r.status_code != 200:
            return _fail(f"FMP returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        # FMP 也可能回 200 + {"Error Message": ...}，光看 status code 會誤判成有效
        if isinstance(data, dict) and data.get("Error Message"):
            return _fail("FMP rejected the key (Invalid API KEY)", 200, latency)
        if isinstance(data, list):
            return _ok("FMP key is valid", 200, latency)
        return _fail("FMP returned an unexpected format (expected a list)", 200, latency)
    except requests.Timeout:
        return _fail("FMP did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"FMP test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_coinmarketcap(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.get(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
            params={"CMC_PRO_API_KEY": api_key, "start": 1, "limit": 1},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code == 401:
            return _fail("CoinMarketCap rejected the key (401)", 401, latency)
        if r.status_code == 429:
            return _fail("CoinMarketCap key is valid but rate limit reached (429)", 429, latency)
        if r.status_code != 200:
            return _fail(f"CoinMarketCap returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        status = data.get("status", {})
        if status.get("error_code") == 0:
            return _ok("CoinMarketCap key is valid", 200, latency)
        return _fail(
            f"CoinMarketCap error_code={status.get('error_code')}: {status.get('error_message', '')}",
            200,
            latency,
        )
    except requests.Timeout:
        return _fail("CoinMarketCap did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"CoinMarketCap test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_etherscan(api_key: str) -> Dict:
    start = time.time()
    try:
        # V2 API + account/balance（免費 key 可用；stats/ethsupply 是 Pro-only）
        r = requests.get(
            "https://api.etherscan.io/v2/api",
            params={
                "chainid": 1,
                "module": "account",
                "action": "balance",
                "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                "apikey": api_key,
            },
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code != 200:
            return _fail(f"Etherscan returned HTTP {r.status_code}", r.status_code, latency)
        data = r.json()
        if data.get("status") == "1":
            return _ok("Etherscan key is valid", 200, latency)
        msg = data.get("message", "unknown error")
        return _fail(f"Etherscan rejected: {msg}", 200, latency)
    except requests.Timeout:
        return _fail("Etherscan did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"Etherscan test failed: {e}", 0, int((time.time() - start) * 1000))


def _test_fred(api_key: str) -> Dict:
    start = time.time()
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series",
            params={"series_id": "GNPCA", "api_key": api_key, "file_type": "json"},
            timeout=_TIMEOUT,
        )
        latency = int((time.time() - start) * 1000)
        if r.status_code == 200:
            return _ok("FRED key is valid", 200, latency)
        if r.status_code == 400:
            try:
                msg = r.json().get("error_message", "Bad Request")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                msg = "Bad Request"
            return _fail(f"FRED rejected the key: {msg}", 400, latency)
        if r.status_code == 403:
            return _fail("FRED rejected the key (403 Forbidden)", 403, latency)
        return _fail(f"FRED returned HTTP {r.status_code}", r.status_code, latency)
    except requests.Timeout:
        return _fail("FRED did not respond (timeout)", 0, int((time.time() - start) * 1000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return _fail(f"FRED test failed: {e}", 0, int((time.time() - start) * 1000))


_TESTERS: Dict[str, Callable[[str], Dict]] = {
    "cryptopanic": _test_cryptopanic,
    "newsapi": _test_newsapi,
    "tavily": _test_tavily,
    "serper": _test_serper,
    "fmp": _test_fmp,
    "coinmarketcap": _test_coinmarketcap,
    "etherscan": _test_etherscan,
    "fred": _test_fred,
}


def test_provider_key(provider: str, api_key: str) -> Dict:
    """
    測試某個 provider 的金鑰是否有效。

    Args:
        provider: tool provider 代號（cryptopanic / newsapi / tavily / serper / fmp /
                  coinmarketcap / etherscan / fred）
        api_key: 要測試的金鑰明文

    Returns:
        {success, message, status_code, latency_ms}
    """
    tester = _TESTERS.get(provider)
    if tester is None:
        return {
            "success": False,
            "message": f"Unsupported provider: {provider}",
            "status_code": 0,
            "latency_ms": 0,
        }
    if not api_key:
        return {
            "success": False,
            "message": "No API key provided",
            "status_code": 0,
            "latency_ms": 0,
        }
    return tester(api_key)
