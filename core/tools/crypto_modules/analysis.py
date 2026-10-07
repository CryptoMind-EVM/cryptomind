"""
價格與分析工具
Technical Analysis, News Analysis, Price Data, Market Movement, Backtest
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import tool

from data.data_fetcher import SymbolNotFoundError, get_data_fetcher
from data.data_processor import (
    analyze_market_structure,
    calculate_key_levels,
    extract_technical_indicators,
    fetch_and_process_klines,
)
from utils.utils import get_crypto_news, safe_float

from ..helpers import find_available_exchange, format_price, normalize_symbol
from ..schemas import (
    MarketPulseInput,
    NewsAnalysisInput,
    PriceInput,
    TechnicalAnalysisInput,
)


@tool(args_schema=TechnicalAnalysisInput)
def technical_analysis_tool(
    symbol: str, interval: str = "1d", exchange: Optional[str] = None
) -> str:
    """執行加密貨幣的純技術分析"""
    try:
        if exchange is None:
            exchange, normalized_symbol = find_available_exchange(symbol)
            if exchange is None:
                return f"Error: could not find a {symbol} trading pair on the supported exchanges."
        else:
            normalized_symbol = normalize_symbol(symbol, exchange)

        df_with_indicators, _ = fetch_and_process_klines(
            symbol=normalized_symbol,
            interval=interval,
            limit=200,
            market_type="spot",
            exchange=exchange,
        )

        latest = df_with_indicators.iloc[-1]
        current_price = safe_float(latest["Close"])
        indicators = extract_technical_indicators(latest)
        market_structure = analyze_market_structure(df_with_indicators)
        trend = market_structure.get("趨勢", "Unknown")
        key_levels = calculate_key_levels(df_with_indicators, period=30)
        support = key_levels.get("支撐位", 0)
        resistance = key_levels.get("壓力位", 0)

        rsi = indicators.get("RSI_14", 50)
        rsi_status = (
            "Overbought"
            if rsi > 70
            else "Oversold"
            if rsi < 30
            else "Relatively strong"
            if rsi > 60
            else "Relatively weak"
            if rsi < 40
            else "Neutral"
        )

        macd = indicators.get("MACD_線", 0)
        macd_status = "Bullish momentum" if macd > 0 else "Bearish momentum" if macd < 0 else "Neutral momentum"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"""## {symbol} Technical Analysis Report ({interval} interval)

### Price
- **Current Price**: {format_price(current_price)}
- **7-Day Trend**: {trend}
- **Volatility**: {market_structure.get("波動率", 0):.2f}%

### Technical Indicators
| Indicator | Value | Interpretation |
| RSI (14) | {rsi:.2f} | {rsi_status} |
| MACD | {macd:.6f} | {macd_status} |
| MA7 | {format_price(indicators.get("MA_7", 0))} | - |
| MA25 | {format_price(indicators.get("MA_25", 0))} | - |

### Key Levels
- **Support Level**: {format_price(support)}
- **Resistance Level**: {format_price(resistance)}

Exchange: {exchange.upper()} | Pair: {normalized_symbol} | Data Time: {ts}
"""
    except SymbolNotFoundError:
        return f"Error: trading pair {symbol} not found."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error during technical analysis: {str(e)}"


@tool(args_schema=NewsAnalysisInput)
def news_analysis_tool(symbol: str, include_sentiment: bool = True) -> str:
    """執行加密貨幣的新聞面分析"""
    try:
        base_symbol = (
            symbol.upper().replace("USDT", "").replace("BUSD", "").replace("-", "")
        )
        news_data = get_crypto_news(symbol=base_symbol, limit=10)

        if not news_data:
            return f"No recent news found for {symbol}."

        positive_keywords = [
            "surge",
            "rally",
            "bullish",
            "gain",
            "rise",
            "up",
            "high",
            "buy",
            "launch",
            "上漲",
            "利好",
            "突破",
            "approval",
            "partnership",
            "adoption",
            "upgrade",
            "halving",
            "ETF",
            "institutional",
        ]
        negative_keywords = [
            "crash",
            "bearish",
            "drop",
            "fall",
            "down",
            "low",
            "sell",
            "hack",
            "scam",
            "下跌",
            "利空",
            "暴跌",
            "ban",
            "regulation",
            "crackdown",
            "dump",
            "lawsuit",
            "delisting",
        ]

        positive_news, negative_news, neutral_news = [], [], []
        for news in news_data[:8]:
            title = news.get("title", "").lower()
            has_positive = any(kw in title for kw in positive_keywords)
            has_negative = any(kw in title for kw in negative_keywords)
            if has_positive and not has_negative:
                positive_news.append(news)
            elif has_negative and not has_positive:
                negative_news.append(news)
            else:
                neutral_news.append(news)

        result = f"## {symbol} Latest News 📰\n\n📊 {len(news_data)} items | 🟢 {len(positive_news)} positive | 🔴 {len(negative_news)} negative\n\n"

        if positive_news:
            result += (
                "### 🟢 Positive News\n"
                + "\n".join([f"- {n.get('title', 'N/A')}" for n in positive_news[:3]])
                + "\n\n"
            )
        if negative_news:
            result += (
                "### 🔴 Negative News\n"
                + "\n".join([f"- {n.get('title', 'N/A')}" for n in negative_news[:3]])
                + "\n"
            )

        return result
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error during news analysis: {str(e)}"


@tool(args_schema=PriceInput)
def get_crypto_price_tool(symbol: str, exchange: Optional[str] = None) -> str:
    """
    查詢加密貨幣即時價格（通用版）。

    優先查 Binance/OKX 等主流交易所；找不到時自動 fallback 到 CoinGecko，
    涵蓋 10000+ 幣種，包含冷門幣與未上主流所的幣（如 PI / PEPE / 小型山寨幣等）。

    Args:
        symbol: 幣種代號（必填，例：BTC, ETH, PI, PEPE, DOGE）
        exchange: 指定交易所（選填，預設自動尋找；若指定則跳過 CoinGecko fallback）
    """
    # ── PATH 1: 主流交易所（Binance / OKX）──
    if exchange is None:
        try:
            ex_found, normalized_symbol = find_available_exchange(symbol)
            if ex_found is not None:
                result = _fetch_from_exchange(symbol, ex_found, normalized_symbol)
                if result is not None:
                    return result
        except SymbolNotFoundError:
            pass
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            import logging as _logging

            _logging.getLogger(__name__).debug(
                f"[get_crypto_price] exchange path failed for {symbol}: {e}"
            )
    else:
        # 用戶顯式指定交易所：只走該交易所路徑
        try:
            normalized_symbol = normalize_symbol(symbol, exchange)
            result = _fetch_from_exchange(symbol, exchange, normalized_symbol)
            if result is not None:
                return result
            return f"Error: could not fetch price data for {symbol} from {exchange}."
        except SymbolNotFoundError:
            return f"Error: trading pair {symbol} not found on {exchange}."
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            return f"Error while querying price: {str(e)}"

    # ── PATH 2: CoinGecko fallback ──
    return _coingecko_price_fallback(symbol)


def _fetch_from_exchange(
    symbol: str, exchange: str, normalized_symbol: str
) -> Optional[str]:
    """從指定交易所取價格；失敗回 None 讓上層 fallback。"""
    try:
        fetcher = get_data_fetcher(exchange)
        klines = fetcher.get_historical_klines(normalized_symbol, "1m", limit=1)

        if klines is None or klines.empty:
            return None

        current_price = safe_float(klines.iloc[-1]["Close"])

        # C-1 修復：交易所 K線極小殘值 sanity check。
        # safe_float 失敗回 0.0；偶發性拿到 pump-dump 殘值（如 0.00001047）
        # 不該當真實價餵給 LLM。<=0 一律視為無效，回 None 走 fallback。
        if not current_price or current_price <= 0:
            import logging as _cg

            _cg.getLogger(__name__).debug(
                "[get_crypto_price] %s 交易所 %s K線 Close=%s (<=0) — 視為無效",
                symbol, exchange, current_price,
            )
            return None

        change_text = "N/A"
        try:
            klines_24h = fetcher.get_historical_klines(
                normalized_symbol, "1h", limit=24
            )
            if klines_24h is not None and len(klines_24h) >= 24:
                price_24h_ago = safe_float(klines_24h.iloc[0]["Close"])
                if price_24h_ago > 0:
                    change_24h = ((current_price / price_24h_ago) - 1) * 100
                    change_text = (
                        f"+{change_24h:.2f}%"
                        if change_24h >= 0
                        else f"{change_24h:.2f}%"
                    )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"## {symbol} Live Price\n\n"
            f"- **Price**: {format_price(current_price)}\n"
            f"- **24h Change**: {change_text}\n"
            f"- **Exchange**: {exchange.upper()} | Data Time: {ts}"
        )
    except SymbolNotFoundError:
        return None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def _coingecko_price_fallback(symbol: str) -> str:
    """
    CoinGecko fallback：用 symbol 搜尋 CoinGecko 並查價。
    含 in-memory cache（TTL 15s）避免限流；429 時回友善訊息。
    """
    import logging as _logging

    import httpx as _httpx

    _logger = _logging.getLogger(__name__)

    cache_key = f"coingecko:price:{symbol.upper()}"
    cached = _coingecko_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Step 1: 搜尋 coin_id（CoinGecko search 按 market_cap 排序，第一個通常最對）
        search_resp = _httpx.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": symbol},
            timeout=10,
        )
        if search_resp.status_code == 429:
            return f"CoinGecko API rate limit reached; please retry in 30 seconds (query: {symbol})"
        if search_resp.status_code != 200:
            return f"Could not fetch the price for {symbol} (CoinGecko search failed with HTTP {search_resp.status_code})"

        coins = search_resp.json().get("coins", [])
        if not coins:
            return f"No market data found for {symbol} (not on CoinGecko either). Please check the symbol."

        # 取 market_cap_rank 最佳（None 排最後）的結果
        coins.sort(
            key=lambda c: c.get("market_cap_rank") if c.get("market_cap_rank") else 9999
        )

        # C-2 修復：CoinGecko search 是文字模糊搜尋，coins[0] 可能是同名仿冒幣。
        # 必須驗證 symbol 完全相符，否則 BTC 可能回傳某仿冒幣（造成 $0.00001047 垃圾價）。
        sym_upper = symbol.strip().upper()
        matched = next(
            (c for c in coins if str(c.get("symbol", "")).strip().upper() == sym_upper),
            None,
        )
        if matched is None:
            # CoinGecko 搜到結果但無任一 symbol 精確相符 → 不可採信（極可能是仿冒幣）
            top_names = ", ".join(
                f"{c.get('symbol','?')}({c.get('name','?')})" for c in coins[:3]
            )
            _logger.warning(
                "[get_crypto_price] CoinGecko search %r 命中但無 symbol 精確相符 "
                "(top: %s) — 拒回仿冒幣", symbol, top_names
            )
            return (
                f"No price data for {symbol}. None of the CoinGecko search results ({top_names}) "
                f"match symbol {symbol} — likely a counterfeit coin; result rejected. Please check the symbol."
            )

        coin_id = matched["id"]
        coin_name = matched.get("name", symbol)
        coin_rank = matched.get("market_cap_rank")

        # Step 2: 查價格
        price_resp = _httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd,twd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
            },
            timeout=10,
        )
        if price_resp.status_code != 200:
            return f"Could not fetch the price for {symbol} (CoinGecko price API failed with HTTP {price_resp.status_code})"

        data = price_resp.json().get(coin_id, {})
        if not data:
            return f"No price data found for {coin_name} ({symbol})."

        usd = data.get("usd")
        twd = data.get("twd")
        change_24h = data.get("usd_24h_change")
        mc = data.get("usd_market_cap")

        # C-1 修復：價格 sanity check。usd 為 None/<=0 是明顯垃圾（無流動性/資料缺失），
        # 不可格式化成「$0.000000」餵給 LLM（會被當真實價格做投資判斷）。
        if not isinstance(usd, (int, float)) or usd <= 0:
            _logger.warning(
                "[get_crypto_price] CoinGecko 回傳 %r USD=%r (<=0/None) — 拒回垃圾價",
                symbol, usd,
            )
            return (
                f"No valid price currently available for {symbol} (the source returned an invalid value). "
                f"The asset may have very low liquidity or temporarily missing data; please try again later."
            )

        # C-1 加強：major coin（top 100）價格異常低極可能是仿冒幣殘值。
        # 例：真 BTC 不可能 < $1。低 rank 幣不在此限（本就可能是微型幣）。
        if isinstance(coin_rank, int) and coin_rank <= 100 and usd < 1.0:
            _logger.warning(
                "[get_crypto_price] %r rank=%s 但價格=$%s < $1 — 疑仿冒幣，拒回",
                symbol, coin_rank, usd,
            )
            return (
                f"Data for {symbol} looks suspicious (top-100 rank but abnormally low price), "
                f"possibly counterfeit-coin data; result rejected. Please check the symbol."
            )

        # 智慧小數：大價幣（≥$1）用 2 位、微小幣用至多 6 位，避免 BTC 印成 $67,000.000000
        usd_text = format_price(usd) if usd != 0 else "N/A"

        change_text = "N/A"
        if isinstance(change_24h, (int, float)):
            change_text = (
                f"+{change_24h:.2f}%" if change_24h >= 0 else f"{change_24h:.2f}%"
            )

        mc_text = "N/A"
        if isinstance(mc, (int, float)) and mc > 0:
            if mc >= 1e9:
                mc_text = f"${mc / 1e9:.2f}B"
            elif mc >= 1e6:
                mc_text = f"${mc / 1e6:.2f}M"
            else:
                mc_text = f"${mc:,.0f}"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = (
            f"## {coin_name} ({symbol.upper()}) Live Price\n\n"
            f"- **Price (USD)**: {usd_text}\n"
            + (
                f"- **Price (TWD)**: NT${twd:,.2f}\n"
                if isinstance(twd, (int, float)) and twd > 0
                else ""
            )
            + f"- **24h Change**: {change_text}\n"
            f"- **Market Cap**: {mc_text}\n"
            f"- **Source**: CoinGecko | Data Time: {ts}"
        )
        _coingecko_cache_set(cache_key, result, ttl=15)
        return result

    except _httpx.TimeoutException:
        return f"CoinGecko API connection timed out (query: {symbol}); please try again later."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        _logger.warning(f"CoinGecko fallback failed for {symbol}: {e}")
        return f"Could not fetch the price for {symbol} (all data sources failed): {type(e).__name__}"


# ── In-memory CoinGecko cache (TTL-based) ──
_coingecko_mem_cache: dict[str, tuple[str, float]] = {}
_COINGECKO_CACHE_LOCK = None  # lazy init


def _coingecko_cache_get(key: str) -> Optional[str]:
    import time as _time

    entry = _coingecko_mem_cache.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if _time.time() > expires_at:
        _coingecko_mem_cache.pop(key, None)
        return None
    return value


def _coingecko_cache_set(key: str, value: str, ttl: int = 15) -> None:
    import time as _time

    _coingecko_mem_cache[key] = (value, _time.time() + ttl)


@tool(args_schema=MarketPulseInput)
def explain_market_movement_tool(symbol: str) -> str:
    """解釋加密貨幣的價格波動原因"""
    try:
        from analysis.market_pulse import get_market_pulse

        base_symbol = symbol.upper().replace("USDT", "").replace("-", "")
        result = get_market_pulse(base_symbol)
        if "error" in result:
            return result["error"]
        return (
            f"### 💡 {base_symbol} Market Pulse\n\n{result.get('explanation', 'No explanation available')}"
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error analyzing market movement: {str(e)}"
