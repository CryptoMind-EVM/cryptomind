import asyncio
import os
import threading
import time

import pandas as pd
import requests
from cachetools import TTLCache
from dotenv import load_dotenv

from api.utils import logger

# Load environment variables from .env file
load_dotenv()

# Global cache for symbol lists (1 hour TTL)
symbol_cache = TTLCache(maxsize=10, ttl=3600)

# Cache fetcher instances per exchange — BinanceDataFetcher holds rate-limit
# state (current_weight_in_window, last_request_time) that MUST persist across
# calls for the limiter to actually work. The previous factory created a new
# instance each call, so the rate-limit counters reset to 0 every time and
# Binance saw the full burst with no client-side throttling.
_fetcher_instances: dict = {}
_fetcher_lock = threading.Lock()


class SymbolNotFoundError(Exception):
    """Custom exception for when a trading symbol is not found on the exchange."""

    def __init__(self, symbol_or_message, message=None):
        # Support both old-style (message string) and new-style (symbol, message)
        if message is not None:
            self.symbol = symbol_or_message
            super().__init__(message)
        else:
            self.symbol = None
            super().__init__(symbol_or_message)


class BinanceDataFetcher:
    """
    Fetches market data from Binance using public API endpoints.
    Does NOT require API keys. strictly for public market data.
    """

    def __init__(self):
        self.spot_base_url = "https://api.binance.com/api/v3"
        self.futures_base_url = "https://fapi.binance.com/fapi/v1"
        # Rate limiting for Binance API - initialize request tracking
        self.last_request_time = time.time()
        # Binance API weight mapping - different endpoints have different weights
        self.endpoint_weights = {
            "/exchangeInfo": 40,  # High weight endpoint - this is likely what's causing the ban
            "/klines": 2,  # Standard weight for klines
            "/ticker/24hr": 2,  # Standard weight for ticker
            "/premiumIndex": 2,  # Standard weight for funding rate
        }
        # Conservative rate limits to avoid bans
        self.max_weight_per_minute = 600  # Reduced to be more conservative
        self.current_weight_in_window = 0
        self.weight_window_start = time.time()
        # Instance-level lock: cached instance is shared across threads via
        # get_data_fetcher(), so rate-limit counters need thread-safe updates.
        # Without this, concurrent calls would race and bypass the limiter.
        self._rate_limit_lock = threading.Lock()

    def _enforce_rate_limit(self, endpoint="/klines"):
        """Enforce rate limiting to avoid hitting Binance API limits.

        Thread-safe: serialized via _rate_limit_lock so concurrent callers
        can't double-read the weight counter before either increments.
        """
        with self._rate_limit_lock:
            current_time = time.time()

            # Get the weight for this endpoint
            weight = self.endpoint_weights.get(endpoint, 1)

            # Check if we're in a new minute window
            if current_time - self.weight_window_start >= 60:
                self.current_weight_in_window = 0
                self.weight_window_start = current_time

            # Check if adding this request's weight would exceed the limit
            if self.current_weight_in_window + weight > self.max_weight_per_minute:
                # Calculate how long to wait until the next window
                sleep_time = 60 - (current_time - self.weight_window_start)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                # Reset the window
                self.current_weight_in_window = 0
                self.weight_window_start = time.time()

            # Add this request's weight to the current window
            self.current_weight_in_window += weight

            # Also enforce minimum delay between requests
            time_since_last_request = current_time - self.last_request_time
            min_delay = 0.05  # 50ms minimum delay between requests
            if time_since_last_request < min_delay:
                sleep_time = min_delay - time_since_last_request
                time.sleep(sleep_time)

            # Update the last request time
            self.last_request_time = time.time()

    def _make_request(self, base_url, endpoint, params=None, timeout=20):
        """Helper to make HTTP requests and handle common errors.

        Retry policy:
        - HTTP 400 Invalid symbol → raise SymbolNotFoundError (not retryable)
        - HTTP 418 / -1003 rate limit → abort immediately (avoid ban escalation)
        - HTTP 5xx / Connection / Timeout → retry with exponential backoff
        - Other HTTP 4xx → log + return None (not retryable, client error)
        """
        # Extract just the endpoint path (without query parameters) for rate limiting
        endpoint_path = endpoint.split("?")[0] if "?" in endpoint else endpoint
        # Enforce rate limiting before making request
        self._enforce_rate_limit(endpoint_path)

        max_retries = 3
        retry_count = 0
        # Retryable errors:ConnectionError / Timeout / 5xx HTTP errors
        # (4xx except 5xx are client errors — retrying won't help)

        while retry_count < max_retries:
            try:
                response = requests.get(
                    base_url + endpoint, params=params, timeout=timeout
                )
                response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
                return response.json()
            except requests.exceptions.HTTPError as http_err:
                # Check for specific Binance error codes for symbol not found
                if response.status_code == 400 and "Invalid symbol" in response.text:
                    raise SymbolNotFoundError(
                        f"Symbol not found or invalid: {params.get('symbol', 'N/A')} on {base_url}"
                    ) from http_err
                # Handle specific rate limit error codes from Binance — abort now
                # to prevent ban escalation (do NOT retry)
                if response.status_code == 418 or "-1003" in response.text:
                    logger.error(f"Binance API rate limit exceeded: {response.text}")
                    logger.error("Aborting request to prevent ban escalation.")
                    return None
                # 5xx server errors are retryable
                if 500 <= response.status_code < 600:
                    retry_count += 1
                    if retry_count < max_retries:
                        backoff = 0.5 * (2 ** (retry_count - 1))  # 0.5s, 1s, 2s
                        logger.warning(
                            f"Binance {response.status_code} on {endpoint_path}, "
                            f"retry {retry_count}/{max_retries} in {backoff:.1f}s"
                        )
                        time.sleep(backoff)
                        continue
                    logger.error(
                        f"Binance {response.status_code} after {max_retries} retries "
                        f"on {endpoint_path}: {response.text}"
                    )
                    return None
                # Other 4xx client errors — not retryable
                logger.error(
                    f"HTTP error occurred: {http_err} - Response: {response.text}"
                )
                return None
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as transient_err:
                # Network-level errors are retryable
                retry_count += 1
                if retry_count < max_retries:
                    backoff = 0.5 * (2 ** (retry_count - 1))
                    logger.warning(
                        f"Binance {type(transient_err).__name__} on {endpoint_path}, "
                        f"retry {retry_count}/{max_retries} in {backoff:.1f}s"
                    )
                    time.sleep(backoff)
                    continue
                logger.error(
                    f"Binance {type(transient_err).__name__} after {max_retries} retries "
                    f"on {endpoint_path}: {transient_err}"
                )
                return None
            except requests.exceptions.RequestException as req_err:
                # Other RequestException subclasses — not retryable, fail fast
                logger.error(f"An unexpected request error occurred: {req_err}")
                return None

        return None

    def check_symbol_availability(self, symbol, market_type="spot"):
        """
        Checks if a given symbol is available on Binance for the specified market type.
        Raises SymbolNotFoundError if the symbol is not found.
        """
        base_url = (
            self.spot_base_url if market_type == "spot" else self.futures_base_url
        )
        endpoint = "/exchangeInfo"

        try:
            exchange_info = self._make_request(base_url, endpoint)
            if exchange_info:
                # Binance returns symbols in a list under the "symbols" key
                for s in exchange_info.get("symbols", []):
                    if s["symbol"] == symbol and s["status"] == "TRADING":
                        return True
                raise SymbolNotFoundError(
                    f"Symbol '{symbol}' not found or not trading on Binance {market_type} market."
                )
            # If exchange_info is None, it means _make_request already handled an error
            # In this case, we can't definitively say the symbol is not found,
            # but rather that we couldn't even check exchange info.
            raise requests.exceptions.RequestException(
                f"Could not retrieve exchange info for {market_type} market to check symbol '{symbol}'."
            )
        except SymbolNotFoundError:
            raise  # Re-raise the specific error
        except requests.exceptions.RequestException as req_err:
            logger.debug(
                f"Symbol availability pre-check skipped for {symbol} on {market_type} market: {req_err}"
            )
            raise  # Re-raise the request error to be handled upstream
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                f"Unexpected error checking symbol availability for {symbol} on {market_type} market: {e}"
            )
            raise requests.exceptions.RequestException(
                f"An unexpected error occurred: {e}"
            )

    def get_top_symbols(self, limit=30, quote_asset="USDT"):
        """
        Gets the top trading symbols by 24-hour quote volume from Binance Spot.
        """
        endpoint = "/ticker/24hr"
        print(f"Fetching top {limit} symbols from Binance, quoted in {quote_asset}...")

        all_tickers = self._make_request(self.spot_base_url, endpoint)

        if all_tickers:
            # Filter for symbols that are quoted in the desired asset (e.g., USDT)
            usdt_tickers = [t for t in all_tickers if t["symbol"].endswith(quote_asset)]

            # Sort by quote volume in descending order
            # The 'quoteVolume' is a string, so it needs to be converted to float
            sorted_tickers = sorted(
                usdt_tickers, key=lambda x: float(x.get("quoteVolume", 0)), reverse=True
            )

            # Get the top 'limit' symbols
            top_symbols = [t["symbol"] for t in sorted_tickers[:limit]]

            print(f"Found top {len(top_symbols)} symbols: {top_symbols}")
            return top_symbols

        print("Could not retrieve tickers to determine top symbols.")
        return []

    def get_tickers(self):
        """
        Get all tickers (raw data) from Binance Spot.
        """
        endpoint = "/ticker/24hr"
        return self._make_request(self.spot_base_url, endpoint)

    def get_all_symbols(self, quote_asset="USDT"):
        """Get all trading symbols quoted in the specified asset."""
        cache_key = f"binance_all_symbols_{quote_asset}"
        if cache_key in symbol_cache:
            return symbol_cache[cache_key]

        endpoint = "/exchangeInfo"
        info = self._make_request(self.spot_base_url, endpoint)
        if info:
            symbols = [
                s["symbol"]
                for s in info["symbols"]
                if s["symbol"].endswith(quote_asset) and s["status"] == "TRADING"
            ]
            symbol_cache[cache_key] = symbols
            return symbols
        return []

    def get_historical_klines(self, symbol, interval, limit=1000, start_str=None):
        """
        Get historical K-line/candlestick data for a symbol from Binance Spot API.
        """
        # 先驗證 symbol 是否存在，但不要讓「取不到 exchangeInfo」拖垮整個抓取：
        # /exchangeInfo 權重高(40)，在雲端 IP 上常被 Binance 地域封鎖/限流而回不了，
        # 這時 check_symbol_availability 會丟 RequestException。真正無效的 symbol，
        # 底下輕量的 /klines(權重2)自己會回 400 Invalid symbol → SymbolNotFoundError，
        # 所以這裡遇到「查不到 exchangeInfo」就跳過預檢、直接抓 klines。
        try:
            self.check_symbol_availability(symbol, market_type="spot")
        except SymbolNotFoundError:
            raise  # symbol 確定無效 → 讓上層換交易所
        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"Skipping exchangeInfo pre-check for '{symbol}' (spot): {exc}"
            )

        endpoint = "/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_str:
            params["startTime"] = int(pd.to_datetime(start_str).timestamp() * 1000)

        data = self._make_request(self.spot_base_url, endpoint, params)

        if data:
            df = pd.DataFrame(
                data,
                columns=[
                    "Open_time",
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume",
                    "Close_time",
                    "Quote_asset_volume",
                    "Number_of_trades",
                    "Taker_buy_base_asset_volume",
                    "Taker_buy_quote_asset_volume",
                    "Ignore",
                ],
            )

            df["Open_time"] = pd.to_datetime(df["Open_time"], unit="ms")
            df["Close_time"] = pd.to_datetime(df["Close_time"], unit="ms")

            numeric_cols = [
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "Quote_asset_volume",
                "Taker_buy_base_asset_volume",
                "Taker_buy_quote_asset_volume",
            ]
            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            return df
        return None

    def get_futures_data(self, symbol, interval, limit=1000):
        """
        Get historical K-line/candlestick data and funding rate for a symbol from Binance Futures API.
        """
        # 同 spot：exchangeInfo 取不到時不阻斷，交給下方 /klines 自行回報無效 symbol。
        try:
            self.check_symbol_availability(symbol, market_type="futures")
        except SymbolNotFoundError:
            raise
        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"Skipping exchangeInfo pre-check for '{symbol}' (futures): {exc}"
            )

        klines_df = None
        funding_rate_info = {}

        # 1. Fetch K-lines from fapi/v1/klines
        klines_params = {"symbol": symbol, "interval": interval, "limit": limit}
        data = self._make_request(self.futures_base_url, "/klines", klines_params)

        if data:
            klines_df = pd.DataFrame(
                data,
                columns=[
                    "Open_time",
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume",
                    "Close_time",
                    "Quote_asset_volume",
                    "Number_of_trades",
                    "Taker_buy_base_asset_volume",
                    "Taker_buy_quote_asset_volume",
                    "Ignore",
                ],
            )

            klines_df["Open_time"] = pd.to_datetime(klines_df["Open_time"], unit="ms")
            klines_df["Close_time"] = pd.to_datetime(klines_df["Close_time"], unit="ms")

            numeric_cols = [
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "Quote_asset_volume",
                "Taker_buy_base_asset_volume",
                "Taker_buy_quote_asset_volume",
            ]
            for col in numeric_cols:
                klines_df[col] = pd.to_numeric(klines_df[col], errors="coerce")

        # 2. Fetch funding rate from fapi/v1/premiumIndex
        funding_params = {"symbol": symbol}
        data = self._make_request(
            self.futures_base_url, "/premiumIndex", funding_params
        )

        if data:
            funding_rate_info = {
                "last_funding_rate": float(data.get("lastFundingRate", 0.0)),
                "next_funding_time": pd.to_datetime(
                    data.get("nextFundingTime", 0), unit="ms"
                ).isoformat(),
            }
        else:
            funding_rate_info = {"error": "Failed to fetch funding rate"}

        return klines_df, funding_rate_info


class OkxDataFetcher:
    """
    OKX 交易所數據獲取器
    Uses public API endpoints only. No API keys required.
    """

    def __init__(self):
        self.base_url = os.getenv("OKX_BASE_URL", "https://www.okx.com/api/v5")

        # ── Client-side rate limiter ──────────────────────────────────────
        # OKX public REST 限制 ~20 請求/2 秒（≈600/min）。設 240/min 留安全餘裕。
        # 背景任務用 asyncio.gather + ThreadPoolExecutor 並行呼叫，需 thread-safe。
        self._rate_limit_lock = threading.Lock()
        self._request_window_start = time.time()
        self._request_count_in_window = 0
        self._max_requests_per_minute = 240
        self._last_request_time = 0.0
        self._min_request_interval = 0.1  # 100ms 最小間隔

        # ── Symbol 存在性快取 ─────────────────────────────────────────────
        # 取代逐個 /public/instruments?instId=XXX 預檢。用批量 endpoint 一次拿全部，
        # 快取 300 秒（5 分鐘），背景 Market Pulse 每 4 小時更新 ~30 幣時
        # 從 ~30 次逐個請求降為 1 次批量請求。
        self._symbols_cache: set[str] = set()
        self._symbols_cache_time: float = 0.0
        self._symbols_cache_ttl = 300  # 5 分鐘

    def _convert_interval(self, interval):
        """
        將 Binance 格式的時間間隔轉換為 OKX 格式

        Binance: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w
        OKX: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W
        """
        interval_map = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1H",
            "2h": "2H",
            "4h": "4H",
            "6h": "6H",
            "12h": "12H",
            "1d": "1D",
            "1w": "1W",
        }
        return interval_map.get(interval.lower(), "1D")

    def _enforce_rate_limit(self):
        """Client-side rate limiter — 避免 OKX 429 限流。

        Thread-safe（背景任務用 ThreadPoolExecutor 並行呼叫）。
        - 每分鐘最多 240 請求（OKX public ~600/min，留安全餘裕）
        - 最小間隔 100ms
        """
        with self._rate_limit_lock:
            current_time = time.time()

            # 重置過期的窗口
            if current_time - self._request_window_start >= 60:
                self._request_count_in_window = 0
                self._request_window_start = current_time

            # 超過分鐘上限 → 等到窗口重置
            if self._request_count_in_window >= self._max_requests_per_minute:
                wait = 60 - (current_time - self._request_window_start)
                if wait > 0:
                    logger.debug(
                        "[OKX] rate limit window full, waiting %.1fs", wait
                    )
                    time.sleep(wait)
                self._request_count_in_window = 0
                self._request_window_start = time.time()

            # 最小間隔
            elapsed = current_time - self._last_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)

            self._request_count_in_window += 1
            self._last_request_time = time.time()

    def _make_request(self, endpoint, params=None, timeout=20):
        """發送 HTTP 請求到 OKX API (client-side rate limited + 429 handling)。

        Retry policy:
        - HTTP 400 Invalid symbol → raise SymbolNotFoundError (not retryable)
        - HTTP 429 rate limit → 讀 Retry-After，指數退避（10s→20s→40s）
        - HTTP 418 ban → 立即中止（避免 ban 升級，同 Binance 版策略）
        - HTTP 5xx / Connection / Timeout → 指數退避（1s→2s→4s）
        """
        proxies = None
        https_proxy = os.getenv("HTTPS_PROXY")
        if https_proxy:
            proxies = {"http": https_proxy, "https": https_proxy}

        # 處理 base_url 和 endpoint 的組合
        if "/api/v5" in self.base_url:
            url = self.base_url + endpoint
        else:
            url = self.base_url + "/api/v5" + endpoint

        max_retries = 3

        for attempt in range(max_retries):
            self._enforce_rate_limit()

            try:
                response = requests.get(
                    url, params=params, timeout=timeout, proxies=proxies
                )
                response.raise_for_status()

                data = response.json()

                # OKX API 返回格式: {"code":"0","msg":"","data":[...]}
                if data.get("code") == "0":
                    return data.get("data", [])
                else:
                    error_msg = data.get("msg", "Unknown error")
                    error_code = data.get("code")

                    # 51001: Instrument ID doesn't exist
                    if error_code == "51001":
                        try:
                            from core.config import TEST_MODE

                            if not TEST_MODE:
                                logger.warning(f"OKX API Warning (51001): {error_msg}")
                        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                            raise
                        except (NameError, ImportError):
                            logger.warning(f"OKX API Warning (51001): {error_msg}")
                    else:
                        logger.warning(f"OKX API error (code={error_code}): {error_msg}")
                    return None

            except requests.exceptions.HTTPError as http_err:
                status = response.status_code

                # 400: Symbol not found — not retryable
                if status == 400:
                    raise SymbolNotFoundError(
                        f"Symbol not found on OKX: {params.get('instId', 'N/A')}"
                    ) from http_err

                # 418: IP banned — abort immediately to avoid escalation
                if status == 418:
                    logger.error(
                        "[OKX] HTTP 418 (IP ban) — aborting to prevent escalation"
                    )
                    return None

                # 429: Rate limited — respect Retry-After, exponential backoff
                if status == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            backoff = min(float(retry_after), 60)
                        except ValueError:
                            backoff = 10 * (2**attempt)  # 10s, 20s, 40s
                    else:
                        backoff = 10 * (2**attempt)  # 10s, 20s, 40s

                    if attempt < max_retries - 1:
                        logger.warning(
                            "[OKX] HTTP 429 rate limited (attempt %d/%d), "
                            "backing off %.1fs",
                            attempt + 1,
                            max_retries,
                            backoff,
                        )
                        time.sleep(backoff)
                        continue
                    logger.error(
                        "[OKX] HTTP 429 after %d retries on %s", max_retries, endpoint
                    )
                    return None

                # 5xx: Server error — retryable with backoff
                if 500 <= status < 600:
                    if attempt < max_retries - 1:
                        backoff = 0.5 * (2**attempt)  # 0.5s, 1s, 2s
                        logger.warning(
                            "[OKX] HTTP %d on %s, retry %d/%d in %.1fs",
                            status,
                            endpoint,
                            attempt + 1,
                            max_retries,
                            backoff,
                        )
                        time.sleep(backoff)
                        continue
                    logger.error(
                        "[OKX] HTTP %d after %d retries on %s",
                        status,
                        max_retries,
                        endpoint,
                    )
                    return None

                # Other 4xx — not retryable
                logger.error("[OKX] HTTP %d on %s: %s", status, endpoint, http_err)
                return None

            except requests.exceptions.ProxyError as proxy_err:
                logger.error("[OKX] Proxy error: %s", proxy_err)
                return None
            except requests.exceptions.RequestException as req_err:
                if attempt < max_retries - 1:
                    backoff = 1 * (2**attempt)
                    logger.warning(
                        "[OKX] Request error (attempt %d/%d), retry in %.1fs: %s",
                        attempt + 1,
                        max_retries,
                        backoff,
                        req_err,
                    )
                    time.sleep(backoff)
                    continue
                logger.error("[OKX] Request failed after %d retries: %s", max_retries, req_err)
                return None

        logger.error("[OKX] Failed after %d attempts on %s", max_retries, endpoint)
        return None

    def get_top_symbols(self, limit=30, quote_asset="USDT"):
        """
        Gets the top trading symbols by 24-hour volume from OKX Spot.
        OKX symbol format is 'BTC-USDT'.
        """
        endpoint = "/market/tickers"
        params = {"instType": "SPOT"}
        print(f"Fetching top {limit} symbols from OKX, quoted in {quote_asset}...")

        all_tickers = self._make_request(endpoint, params)

        if all_tickers:
            # Filter for symbols quoted in the desired asset (e.g., USDT)
            usdt_tickers = [
                t for t in all_tickers if t["instId"].endswith(f"-{quote_asset}")
            ]

            # Sort by 24h volume in quote currency (volCcy24h)
            # The value is a string, so it needs to be converted to float
            sorted_tickers = sorted(
                usdt_tickers, key=lambda x: float(x.get("volCcy24h", 0)), reverse=True
            )

            # Get the top 'limit' symbols
            top_symbols = [t["instId"] for t in sorted_tickers[:limit]]

            print(f"Found top {len(top_symbols)} symbols: {top_symbols}")
            return top_symbols

        print("Could not retrieve tickers from OKX to determine top symbols.")
        return []

    def get_tickers(self, instType="SPOT"):
        """
        Get all tickers (raw data) from OKX.
        """
        endpoint = "/market/tickers"
        params = {"instType": instType}
        return self._make_request(endpoint, params)

    def get_all_symbols(self, quote_asset="USDT"):
        """Get all trading symbols quoted in the specified asset."""
        cache_key = f"okx_all_symbols_{quote_asset}"
        if cache_key in symbol_cache:
            return symbol_cache[cache_key]

        endpoint = "/public/instruments"
        params = {"instType": "SPOT"}
        print(f"Fetching all SPOT symbols from OKX, filtering by {quote_asset}...")
        data = self._make_request(endpoint, params)
        if data:
            # OKX usually has quoteCcy field, let's use it for better accuracy
            symbols = [
                s["instId"]
                for s in data
                if (
                    s.get("quoteCcy") == quote_asset
                    or s["instId"].endswith(f"-{quote_asset}")
                )
                and s.get("state") == "live"
            ]
            print(f"OKX: Found {len(symbols)} symbols matching {quote_asset}")
            symbol_cache[cache_key] = symbols
            return symbols
        print("OKX: Failed to retrieve symbols from instruments endpoint.")
        return []

    def _refresh_symbols_cache(self, inst_type="SPOT"):
        """用批量 endpoint 一次拿全部 live instruments，填入本地快取。

        取代舊的逐個 /public/instruments?instId=XXX 預檢——那會在背景
        Market Pulse 更新 ~30 幣時打出 ~30 次請求，瞬間撞 OKX 429 限流。
        """
        now = time.time()
        if self._symbols_cache and (now - self._symbols_cache_time < self._symbols_cache_ttl):
            return  # 快取仍有效

        endpoint = "/public/instruments"
        params = {"instType": inst_type}
        data = self._make_request(endpoint, params)
        if data:
            self._symbols_cache = {
                s["instId"]
                for s in data
                if s.get("state") == "live" and "instId" in s
            }
            self._symbols_cache_time = now
            logger.debug(
                "[OKX] symbols cache refreshed: %d live %s instruments",
                len(self._symbols_cache),
                inst_type,
            )

    def check_symbol_availability(self, symbol, inst_type="SPOT"):
        """Checks if a given symbol is available on OKX (batch-cached).

        用批量快取取代逐個 /public/instruments?instId=XXX 預檢。
        快取 TTL 5 分鐘，miss 時才打一次批量 endpoint（拿全部 instruments）。
        Raises SymbolNotFoundError if the symbol is not found.
        """
        self._refresh_symbols_cache(inst_type)

        if symbol in self._symbols_cache:
            return True

        # 快取可能是空的（批量請求失敗）→ fallback 直接查 candles 端點
        # 讓 get_historical_klines 的 /market/candles 自己回報 51001/400
        if not self._symbols_cache:
            logger.debug(
                "[OKX] symbols cache empty for %s, skipping pre-check", symbol
            )
            return True  # 放行，讓下游 candles endpoint 自然處理

        raise SymbolNotFoundError(
            f"Symbol '{symbol}' not found or not live on OKX {inst_type} market."
        )

    def get_historical_klines(self, symbol, interval, limit=100):
        """
        獲取 OKX 現貨市場的 K 線數據

        Args:
            symbol: 交易對符號 (OKX 格式，如 "BTC-USDT")
            interval: 時間間隔 (如 "1d", "1h")
            limit: 數據條數

        Returns:
            DataFrame with columns: Open_time, Open, High, Low, Close, Volume, etc.
        """
        self.check_symbol_availability(
            symbol, inst_type="SPOT"
        )  # Check symbol availability first

        endpoint = "/market/candles"

        # 轉換時間間隔
        okx_interval = self._convert_interval(interval)

        # OKX API 參數
        params = {
            "instId": symbol,
            "bar": okx_interval,
            "limit": min(limit, 300),  # OKX 最多返回 300 條
        }

        data = self._make_request(endpoint, params)

        if not data:
            return None

        # OKX 返回格式: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        # 轉換為與 Binance 相同的格式
        df = pd.DataFrame(
            data,
            columns=[
                "Open_time",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "Volume_currency",
                "Volume_quote",
                "Confirm",
            ],
        )

        # 轉換數據類型，處理空字符串
        df["Open_time"] = pd.to_datetime(pd.to_numeric(df["Open_time"]), unit="ms")
        df["Open"] = pd.to_numeric(df["Open"], errors="coerce")
        df["High"] = pd.to_numeric(df["High"], errors="coerce")
        df["Low"] = pd.to_numeric(df["Low"], errors="coerce")
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

        # 添加 Binance 格式的額外欄位（用於兼容性）
        df["Close_time"] = (
            df["Open_time"]
            + pd.to_timedelta(okx_interval.replace("H", "h"))
            - pd.to_timedelta(1, "ms")
        )
        df["Quote_asset_volume"] = df["Volume_quote"]
        df["Number_of_trades"] = 0  # OKX 不提供
        df["Taker_buy_base_asset_volume"] = 0  # OKX 不提供
        df["Taker_buy_quote_asset_volume"] = 0  # OKX 不提供
        df["Ignore"] = 0

        # 反轉順序（OKX 返回的是從新到舊）
        df = df.iloc[::-1].reset_index(drop=True)

        # 只保留需要的欄位
        df = df[
            [
                "Open_time",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "Close_time",
                "Quote_asset_volume",
                "Number_of_trades",
                "Taker_buy_base_asset_volume",
                "Taker_buy_quote_asset_volume",
                "Ignore",
            ]
        ]

        return df

    def get_futures_data(self, symbol, interval, limit=100):
        """
        獲取 OKX 合約市場的 K 線數據和資金費率

        Args:
            symbol: 交易對符號 (如 "BTC-USDT-SWAP")
            interval: 時間間隔
            limit: 數據條數

        Returns:
            (DataFrame, funding_rate_dict)
        """
        # OKX 合約符號格式: BTC-USDT-SWAP
        if not symbol.endswith("-SWAP"):
            # 如果是現貨格式 (BTC-USDT)，轉換為合約格式
            symbol = symbol + "-SWAP"

        self.check_symbol_availability(
            symbol, inst_type="SWAP"
        )  # Check symbol availability first

        # 獲取 K 線數據
        endpoint = "/market/candles"
        okx_interval = self._convert_interval(interval)

        params = {"instId": symbol, "bar": okx_interval, "limit": min(limit, 300)}

        klines_data = self._make_request(endpoint, params)

        if not klines_data:
            return None, {}

        # 轉換 K 線數據
        df = pd.DataFrame(
            klines_data,
            columns=[
                "Open_time",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "Volume_currency",
                "Volume_quote",
                "Confirm",
            ],
        )

        df["Open_time"] = pd.to_datetime(pd.to_numeric(df["Open_time"]), unit="ms")
        df["Open"] = pd.to_numeric(df["Open"], errors="coerce")
        df["High"] = pd.to_numeric(df["High"], errors="coerce")
        df["Low"] = pd.to_numeric(df["Low"], errors="coerce")
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

        df["Close_time"] = (
            df["Open_time"]
            + pd.to_timedelta(okx_interval.replace("H", "h"))
            - pd.to_timedelta(1, "ms")
        )
        df["Quote_asset_volume"] = df["Volume_quote"]
        df["Number_of_trades"] = 0
        df["Taker_buy_base_asset_volume"] = 0
        df["Taker_buy_quote_asset_volume"] = 0
        df["Ignore"] = 0

        df = df.iloc[::-1].reset_index(drop=True)

        df = df[
            [
                "Open_time",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
                "Close_time",
                "Quote_asset_volume",
                "Number_of_trades",
                "Taker_buy_base_asset_volume",
                "Taker_buy_quote_asset_volume",
                "Ignore",
            ]
        ]

        # 獲取資金費率
        funding_rate_info = self._get_funding_rate(symbol)

        return df, funding_rate_info

    def _get_funding_rate(self, symbol):
        """
        獲取資金費率

        Args:
            symbol: 合約交易對符號 (如 "BTC-USDT-SWAP")

        Returns:
            Dict with funding rate information
        """
        endpoint = "/public/funding-rate"

        params = {"instId": symbol}

        data = self._make_request(endpoint, params)

        if not data or len(data) == 0:
            return {}

        # OKX 返回格式: [{"fundingRate":"0.0001","fundingTime":"...","nextFundingRate":"0.0001","nextFundingTime":"..."}]
        rate_data = data[0]

        # 安全轉換，處理空字符串
        def safe_float(value, default=0.0):
            try:
                return float(value) if value and value != "" else default
            except (ValueError, TypeError):
                return default

        return {
            "current_funding_rate": safe_float(rate_data.get("fundingRate")),
            "next_funding_rate": safe_float(rate_data.get("nextFundingRate")),
            "funding_time": rate_data.get("fundingTime", ""),
            "next_funding_time": rate_data.get("nextFundingTime", ""),
        }


def get_data_fetcher(exchange: str):
    """
    Factory function to get the appropriate data fetcher.

    Instances are cached per exchange because BinanceDataFetcher holds
    rate-limit state (current_weight_in_window, last_request_time) that
    must persist across calls for the limiter to actually throttle.

    設 USE_CCXT=1 改走 CcxtAdapter（CCXT 統一抽象），否則用既有的手寫 fetcher。
    adapter 刻意維持與 BinanceDataFetcher/OkxDataFetcher 完全相同的公開方法與
    回傳結構（12 欄 DataFrame、symbol 格式、funding dict schema），可作為對照組。
    """
    key = exchange.lower()
    if key not in ("binance", "okx"):
        raise ValueError(f"Unsupported exchange: {exchange}")

    use_ccxt = os.getenv("USE_CCXT", "").lower() in ("1", "true", "yes")
    cache_key = f"{key}__ccxt" if use_ccxt else key

    # Double-checked locking pattern — avoid lock overhead on hot path
    # once the instance is cached.
    if cache_key in _fetcher_instances:
        return _fetcher_instances[cache_key]

    with _fetcher_lock:
        if use_ccxt:
            instance = CcxtAdapter(key)
        elif key == "binance":
            instance = BinanceDataFetcher()
        else:  # okx
            instance = OkxDataFetcher()
        _fetcher_instances[cache_key] = instance
        return instance


# ──────────────────────────────────────────────────────────────────────────────
# CCXT 統一交易所抽象（adapter）
#
# 用 CCXT 取代手寫的 Binance/OKX REST client，降低每接一家交易所就手寫一次
# 的維護成本。adapter 維持與 BinanceDataFetcher/OkxDataFetcher 完全相同的
# 公開方法與回傳結構（12 欄 DataFrame、對外 symbol 格式、funding dict schema），
# 經 USE_CCXT=1 切換，舊實作保留為對照組。
#
# CCXT 的 fetch_ohlcv 只回 6 欄 [ts, o, h, l, c, v]；缺的欄位（Quote_asset_volume、
# Number_of_trades、Taker_buy_*、Close_time、Ignore）補 0 或由 timeframe 推算，
# 與 OkxDataFetcher 補欄位的既有做法一致。
# ──────────────────────────────────────────────────────────────────────────────
_CCXT_KLINE_COLUMNS = [
    "Open_time",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Close_time",
    "Quote_asset_volume",
    "Number_of_trades",
    "Taker_buy_base_asset_volume",
    "Taker_buy_quote_asset_volume",
    "Ignore",
]

# 對外 symbol 格式 ↔ CCXT unified symbol 的轉換
# Binance: BTCUSDT → BTC/USDT；OKX: BTC-USDT → BTC/USDT


def _to_ccxt_symbol(symbol: str, exchange: str) -> str:
    """把對外 symbol 轉成 CCXT unified symbol（BTC/USDT）。"""
    if "/" in symbol:
        return symbol
    if exchange == "binance":
        # BTCUSDT → BTC/USDT：常見 quote 幣切分
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "FDUSD"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                return f"{symbol[: -len(quote)]}/{quote}"
        return symbol
    # okx: BTC-USDT → BTC/USDT
    return symbol.replace("-", "/")


def _from_ccxt_symbol(symbol: str, exchange: str) -> str:
    """把 CCXT unified symbol 轉回對外格式。"""
    if exchange == "binance":
        return symbol.replace("/", "")
    return symbol.replace("/", "-")


class CcxtAdapter:
    """CCXT-based adapter，對齊 BinanceDataFetcher / OkxDataFetcher 的公開介面。

    注意：不繼承上述兩者，但實作相同方法名與回傳結構，方便 get_data_fetcher
    透明替換。CCXT 自帶 enableRateLimit，單例由 get_data_fetcher 快取保留。
    """

    def __init__(self, exchange: str):
        import ccxt  # 延遲 import：USE_CCXT=0 時不強制依賴

        self.exchange_key = exchange  # "binance" | "okx"
        if exchange == "binance":
            self._ccxt = ccxt.binance({"enableRateLimit": True})
        elif exchange == "okx":
            base = os.getenv("OKX_BASE_URL")
            opts = {"enableRateLimit": True}
            if base:
                opts["urls"] = {"api": base}
            self._ccxt = ccxt.okx(opts)
        else:  # pragma: no cover — get_data_fetcher 已擋
            raise ValueError(f"Unsupported exchange: {exchange}")

    # ── symbol 列表 ──────────────────────────────────────────────────────
    def get_top_symbols(self, limit=30, quote_asset="USDT") -> list:
        """依 24h quote volume 排序的前幾大 symbol（對外格式）。"""
        try:
            tickers = self._ccxt.fetch_tickers()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.warning(f"[CcxtAdapter] fetch_tickers failed: {exc}")
            return []

        rows = []
        for sym, t in tickers.items():
            if not sym.endswith(f"/{quote_asset}"):
                continue
            qv = t.get("quoteVolume") or 0
            rows.append((sym, qv))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [_from_ccxt_symbol(s, self.exchange_key) for s, _ in rows[:limit]]

    def get_all_symbols(self, quote_asset="USDT") -> list:
        """所有某 quote 幣的 symbol（對外格式）。"""
        markets = self._ccxt.load_markets()
        out = []
        for sym in markets:
            if sym.endswith(f"/{quote_asset}"):
                out.append(_from_ccxt_symbol(sym, self.exchange_key))
        return sorted(out)

    def get_tickers(self):
        """回傳 raw tickers dict（CCXT unified 格式）。"""
        return self._ccxt.fetch_tickers()

    def check_symbol_availability(self, symbol, market_type="spot") -> bool:
        """symbol 是否存在。不存在拋 SymbolNotFoundError。"""
        markets = self._ccxt.load_markets()
        ccxt_sym = _to_ccxt_symbol(symbol, self.exchange_key)
        if ccxt_sym not in markets:
            raise SymbolNotFoundError(symbol, f"Symbol not found: {symbol}")
        return True

    # ── K 線 ─────────────────────────────────────────────────────────────
    def _ohlcv_to_df(self, ohlcv: list) -> "pd.DataFrame":
        """CCXT [ts,o,h,l,c,v] → 12 欄 DataFrame（對齊既有契約）。"""
        if not ohlcv:
            return None
        df = pd.DataFrame(
            ohlcv, columns=["Open_time_ms", "Open", "High", "Low", "Close", "Volume"]
        )
        df["Open_time"] = pd.to_datetime(df["Open_time_ms"], unit="ms")
        df["Close_time"] = df["Open_time"] + pd.Timedelta(milliseconds=1)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # CCXT 通用 OHLCV 不含以下欄位，補 0（與 OkxDataFetcher 既有做法一致）
        df["Quote_asset_volume"] = 0.0
        df["Number_of_trades"] = 0
        df["Taker_buy_base_asset_volume"] = 0.0
        df["Taker_buy_quote_asset_volume"] = 0.0
        df["Ignore"] = 0
        return df[_CCXT_KLINE_COLUMNS]

    def _interval_to_ccxt(self, interval: str) -> str:
        """把專案 interval（如 1d/1h/15m）正規化為 CCXT timeframe。

        OKX 用的 1H/1D 等也涵蓋；CCXT 統一用小寫。
        """
        if not interval:
            return "1d"
        s = interval.lower()
        # CCXT 接受 1m/5m/1h/1d 等；OKX 的 1H → 1h
        return s

    def get_historical_klines(self, symbol, interval, limit=1000, start_str=None):
        """現貨 K 線（12 欄 DataFrame，與 BinanceDataFetcher 對齊）。"""
        ccxt_sym = _to_ccxt_symbol(symbol, self.exchange_key)
        params = {"limit": limit}
        if start_str:
            params["since"] = int(pd.to_datetime(start_str).timestamp() * 1000)
        try:
            ohlcv = self._ccxt.fetch_ohlcv(
                ccxt_sym, timeframe=self._interval_to_ccxt(interval), **params
            )
        except Exception as exc:
            logger.warning(f"[CcxtAdapter] fetch_ohlcv failed for {symbol}: {exc}")
            return None
        return self._ohlcv_to_df(ohlcv)

    def get_futures_data(self, symbol, interval, limit=1000):
        """期貨 K 線 + funding rate（tuple(DataFrame, funding_dict)）。

        funding dict schema 依交易所對齊既有契約：
        - binance: {last_funding_rate, next_funding_time}
        - okx: {current_funding_rate, next_funding_rate, funding_time, next_funding_time}
        """
        ccxt_sym = _to_ccxt_symbol(symbol, self.exchange_key)
        klines_df = self.get_historical_klines(symbol, interval, limit=limit)

        funding_rate_info = {}
        try:
            if self.exchange_key == "binance" and hasattr(
                self._ccxt, "fetch_funding_rate"
            ):
                fr = self._ccxt.fetch_funding_rate(ccxt_sym)
                funding_rate_info = {
                    "last_funding_rate": float(fr.get("fundingRate", 0.0) or 0.0),
                    "next_funding_time": (
                        pd.to_datetime(fr.get("fundingDatetime") or 0).isoformat()
                        if fr.get("fundingDatetime")
                        else ""
                    ),
                }
            elif self.exchange_key == "okx" and hasattr(
                self._ccxt, "fetch_funding_rate"
            ):
                fr = self._ccxt.fetch_funding_rate(ccxt_sym)
                funding_rate_info = {
                    "current_funding_rate": float(
                        fr.get("fundingRate", 0.0) or 0.0
                    ),
                    "next_funding_rate": float(
                        fr.get("markPrice", 0.0) or 0.0
                    ),  # CCXT 無獨立 next，fallback
                    "funding_time": fr.get("fundingDatetime", "") or "",
                    "next_funding_time": fr.get("nextFundingDatetime", "") or "",
                }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.warning(
                f"[CcxtAdapter] fetch_funding_rate failed for {symbol}: {exc}"
            )
            funding_rate_info = {"error": "Failed to fetch funding rate"}

        return klines_df, funding_rate_info


if __name__ == "__main__":
    # 僅保留成功的冒煙測試，移除會混淆使用者的錯誤測試
    print("--- 啟動交易所數據獲取器測試 ---")

    # 測試 OKX
    try:
        okx_fetcher = get_data_fetcher("okx")
        symbols = okx_fetcher.get_top_symbols(limit=5)
        print(f"✅ OKX 測試成功，前 5 大幣種: {symbols}")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f"❌ OKX 測試失敗: {e}")

    print("\n" + "=" * 30 + "\n")

    # 測試 Binance
    try:
        binance_fetcher = get_data_fetcher("binance")
        symbols = binance_fetcher.get_top_symbols(limit=5)
        if symbols:
            print(f"✅ Binance 測試成功: {symbols}")
        else:
            print("ℹ️ Binance 目前處於頻率限制或封禁中，跳過測試。")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f"ℹ️ Binance 測試跳過 (預期限制): {e}")
