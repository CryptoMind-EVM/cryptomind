"""
DeFi 工具
DefiLlama TVL, Categories & Gainers, Token Unlocks, Token Supply
"""

import asyncio
from typing import Dict

import httpx
from langchain_core.tools import tool

from ..helpers import extract_crypto_symbols
from ..schemas import ExtractCryptoSymbolsInput
from .common import get_cached_data, set_cached_data


@tool
def get_defillama_tvl(protocol_name: str) -> str:
    """從 DefiLlama 獲取特定協議或公鏈的 TVL"""
    try:
        slug = protocol_name.strip().lower().replace(" ", "-")
        resp = httpx.get(f"https://api.llama.fi/protocol/{slug}", timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            name = data.get("name", protocol_name)
            current_chain_tvls = data.get("currentChainTvls", {})
            tvl = sum(current_chain_tvls.values()) if current_chain_tvls else 0
            if tvl == 0:
                tvl_data = data.get("tvl", [])
                tvl = tvl_data[-1].get("totalLiquidityUSD", 0) if tvl_data else 0

            if tvl > 0:
                tvl_str = (
                    f"${tvl / 1_000_000_000:.2f}B"
                    if tvl > 1_000_000_000
                    else f"${tvl / 1_000_000:.2f}M"
                )
                return f"## 🏦 DefiLlama TVL\n\n- **Protocol**: {name}\n- **TVL**: {tvl_str}\n\n*(Source: DefiLlama)*"

        # Try as chain
        chains_resp = httpx.get("https://api.llama.fi/v2/chains", timeout=10)
        if chains_resp.status_code == 200:
            for chain in chains_resp.json():
                if (
                    chain.get("name", "").lower() == slug
                    or chain.get("tokenSymbol", "").lower() == slug
                ):
                    tvl = chain.get("tvl", 0)
                    tvl_str = (
                        f"${tvl / 1_000_000_000:.2f}B"
                        if tvl > 1_000_000_000
                        else f"${tvl / 1_000_000:.2f}M"
                    )
                    return f"## 🏦 {chain.get('name')} TVL\n\n- **TVL**: {tvl_str}\n\n*(Source: DefiLlama)*"

        return f"No data found for '{protocol_name}'."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error fetching TVL: {str(e)}"


@tool
def get_crypto_categories_and_gainers() -> str:
    """獲取加密貨幣板塊與領漲幣種"""
    cache_key = "categories_and_gainers"
    cached = get_cached_data(cache_key, 300)
    if cached:
        return cached

    try:
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/coins/categories", timeout=10
        )
        if resp.status_code == 200:
            categories = resp.json()
            sorted_cats = sorted(
                [c for c in categories if c.get("market_cap_change_24h") is not None],
                key=lambda x: x["market_cap_change_24h"],
                reverse=True,
            )
            output = "## 🚀 Top Sectors\n\n"
            for i, cat in enumerate(sorted_cats[:5], 1):
                output += f"{i}. **{cat.get('name')}**: {cat.get('market_cap_change_24h', 0):+.2f}%\n"
            final = output + "\n*(Source: CoinGecko)*"
            set_cached_data(cache_key, final)
            return final
        return "Unable to fetch sector data."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_token_unlocks(symbol: str) -> str:
    """查代幣未來 90 天的解鎖排程（DefiLlama 公開資料，cliff 事件）。

    回每次解鎖的日期、數量、佔流通量比例、分配類別。查不到就明說沒有排程資料，
    不要猜日期。BTC／ETH 這類沒有解鎖機制的會直接說明。

    Args:
        symbol: 代幣代號，如 ARB、SUI、APT。
    """
    from datetime import datetime, timezone

    from core.market_events.token_unlocks import (
        format_amount,
        get_unlock_index,
        upcoming_unlocks,
    )

    symbol = (symbol or "").strip().upper()
    if not symbol:
        return "Error: symbol is required."
    if symbol in ("BTC", "ETH"):
        return f"✅ {symbol} has no scheduled token unlocks (no vesting/cliff schedule)."
    index = get_unlock_index()
    if not index:
        return "⚠️ Unlock schedule source (DefiLlama) is unavailable right now; no unlock data."
    entry = index.get(symbol)
    if not entry:
        return (
            f"ℹ️ DefiLlama has no unlock schedule for {symbol} "
            "(token not tracked, or fully unlocked). Do not assume any unlock date."
        )
    today = datetime.now(timezone.utc).date()
    events = upcoming_unlocks(symbol, today=today, days=90, index=index)
    circ = float(entry.get("circ") or 0)
    head = (
        f"**{symbol} unlock schedule** (source: DefiLlama, protocol={entry.get('slug')}, "
        f"circulating={format_amount(circ, 'en')})"
    )
    if not events:
        return f"{head}\n\n✅ No cliff unlocks in the next 90 days."
    lines = [head, ""]
    for ev in events:
        pct = f"{ev['pct_of_circ']:.2f}%" if ev.get("pct_of_circ") is not None else "n/a"
        usd = f" (~${ev['usd']:,.0f})" if ev.get("usd") else ""
        who = ", ".join(ev.get("recipients") or ev.get("categories") or []) or "unspecified"
        lines.append(
            f"- {ev['date'].isoformat()}: {format_amount(ev['amount'], 'en')} {symbol}"
            f"{usd} — {pct} of circulating — {who}"
        )
    return "\n".join(lines)


@tool
def get_token_supply(symbol: str) -> str:
    """獲取代幣供應量數據"""
    symbol = symbol.upper()
    cache_key = f"token_supply_{symbol}"
    cached = get_cached_data(cache_key, 600)
    if cached:
        return cached

    try:
        search_resp = httpx.get(
            f"https://api.coingecko.com/api/v3/search?query={symbol}", timeout=10
        )
        coins = search_resp.json().get("coins", [])
        if not coins:
            return f"{symbol} not found."

        coin_id = coins[0]["id"]
        for c in coins:
            if c.get("symbol", "").upper() == symbol:
                coin_id = c["id"]
                break

        detail_resp = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&market_data=true",
            timeout=10,
        )
        md = detail_resp.json().get("market_data", {})

        def fmt(v):
            if v is None:
                return "Unknown"
            return (
                f"{v / 1_000_000_000:.2f}B"
                if v > 1_000_000_000
                else f"{v / 1_000_000:.2f}M"
            )

        result = f"## 🪙 {symbol} Supply\n\n- Circulating: {fmt(md.get('circulating_supply'))}\n- Total: {fmt(md.get('total_supply'))}\n- Max: {fmt(md.get('max_supply'))}\n\n*(Source: CoinGecko)*"
        set_cached_data(cache_key, result)
        return result
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_crypto_market_cap() -> str:
    """獲取加密貨幣市值排行 Top 100（使用 CoinGecko 免費 API）"""
    cache_key = "crypto_market_cap_top100"
    cached = get_cached_data(cache_key, 300)
    if cached:
        return cached

    try:
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=market_cap_desc"
            "&per_page=100&sparkline=false",
            timeout=15,
        )
        if resp.status_code == 429:
            return "CoinGecko API rate limit reached; please try again later."
        if resp.status_code != 200:
            return "Unable to fetch market cap rankings (API error)"

        coins = resp.json()
        output = "## 🏆 Top 100 Cryptocurrencies by Market Cap\n\n"
        output += "| # | Coin | Price | Market Cap | 24h Change |\n|---|---|---|---|---|\n"

        for i, coin in enumerate(coins, 1):
            symbol = (coin.get("symbol") or "").upper()
            name = coin.get("name") or ""
            price = coin.get("current_price") or 0
            mcap = coin.get("market_cap") or 0
            change = coin.get("price_change_percentage_24h")

            if price >= 1:
                price_s = f"${price:,.2f}"
            elif price >= 0.0001:
                price_s = f"${price:.6f}"
            else:
                price_s = f"${price:.8f}"

            if mcap >= 1_000_000_000:
                cap_s = f"${mcap / 1_000_000_000:.2f}B"
            elif mcap >= 1_000_000:
                cap_s = f"${mcap / 1_000_000:.2f}M"
            else:
                cap_s = f"${mcap:,.0f}"

            change_s = f"{change:+.2f}%" if change is not None else "N/A"

            label = f"**{symbol}**" if i <= 10 else symbol
            output += (
                f"| {i} | {label} — {name[:20]} | {price_s} | {cap_s} | {change_s} |\n"
            )

            # Truncate at 30 for readability
            if i >= 30:
                output += "\n| ... | *(next 70 coins omitted)* | ... | ... | ... |\n"
                break

        output += "\n*(Source: CoinGecko)*"
        set_cached_data(cache_key, output)
        return output
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_dex_volume() -> str:
    """查詢去中心化交易所交易量排行（使用 DeFiLlama 免費 API）"""
    cache_key = "dex_volume_ranking"
    cached = get_cached_data(cache_key, 300)
    if cached:
        return cached

    try:
        resp = httpx.get(
            "https://api.llama.fi/overview/dexs"
            "?excludeTotalDataChart=true"
            "&excludeTotalDataChartBreakdown=true",
            timeout=15,
        )
        if resp.status_code != 200:
            return "Unable to fetch DEX volume data (API error)"

        data = resp.json()
        dex_list = data if isinstance(data, list) else data.get("protocols", [])

        if not dex_list:
            return "No DEX volume data available at the moment."

        output = "## 🔄 DEX Trading Volume Rankings\n\n"
        output += "| # | DEX | Chain | 24h Volume | 7d Volume | 24h Change |\n"
        output += "|---|---|---|---|---|---|\n"

        for i, dex in enumerate(dex_list[:20], 1):
            name = dex.get("name") or dex.get("displayName") or f"DEX #{i}"
            chain = dex.get("chain") or "Multi"
            volume_24h = dex.get("volume24h") or dex.get("totalVolume24h") or 0
            volume_7d = dex.get("volume7d") or dex.get("totalVolume7d") or 0
            change_24h = dex.get("changeVolume24h")

            def _fmt(v):
                if v >= 1_000_000_000:
                    return f"${v / 1_000_000_000:.2f}B"
                if v >= 1_000_000:
                    return f"${v / 1_000_000:.2f}M"
                return f"${v:,.0f}"

            change_s = f"{change_24h:+.2f}%" if change_24h is not None else "N/A"
            output += f"| {i} | **{name}** | {chain} | {_fmt(volume_24h)} | {_fmt(volume_7d)} | {change_s} |\n"

        output += "\n*(Source: DeFiLlama)*"
        set_cached_data(cache_key, output)
        return output
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"


@tool(args_schema=ExtractCryptoSymbolsInput)
def extract_crypto_symbols_tool(user_query: str) -> Dict:
    """從查詢中提取加密貨幣符號"""
    symbols = extract_crypto_symbols(user_query)
    return {
        "original_query": user_query,
        "extracted_symbols": symbols,
        "count": len(symbols),
    }


@tool
def get_staking_yield(symbol: str) -> str:
    """獲取加密貨幣的質押年化收益率（APY）。

    使用 DefiLlama Yields API 獲取實時質押/借貸收益率數據。
    支援任何在 DeFi 協議中有質押池的代幣。
    """
    symbol = symbol.upper()
    cache_key = f"staking_yield_{symbol}"
    cached = get_cached_data(cache_key, 600)
    if cached:
        return cached

    try:
        # 使用 DefiLlama Yields API - 獲取所有質押池數據
        resp = httpx.get("https://yields.llama.fi/pools", timeout=15)

        if resp.status_code != 200:
            return "Unable to fetch staking yield data (API error)"

        all_pools = resp.json().get("data", [])

        # 篩選與該代幣相關的質押池
        # 优先級：原生質押 > LST > 借貸
        relevant_pools = []
        for pool in all_pools:
            pool_symbol = pool.get("symbol", "").upper()
            underlying = pool.get("underlyingTokens") or []  # 確保不是 None
            pool_name = pool.get("poolName", "").upper()
            chain = pool.get("chain", "")

            # 檢查是否匹配目標代幣
            is_match = (
                symbol == pool_symbol
                or symbol in pool_name
                or (underlying and any(symbol == t.upper() for t in underlying))
                or f"{symbol}2" in pool_symbol  # stETH, stSOL 等
                or f"S{symbol}" in pool_symbol
            )

            if is_match:
                apy = pool.get("apy", 0) or 0
                tvl = pool.get("tvlUsd", 0) or 0
                pool_type = (
                    pool.get("apyBaseBorrow", None) is not None and "Lending" or "Staking"
                )
                if (
                    "stake" in pool.get("poolName", "").lower()
                    or "staking" in pool.get("poolName", "").lower()
                ):
                    pool_type = "Native Staking"

                relevant_pools.append(
                    {
                        "pool": pool.get("poolName", "Unknown"),
                        "project": pool.get("project", ""),
                        "chain": chain,
                        "apy": apy,
                        "tvl": tvl,
                        "type": pool_type,
                    }
                )

        if not relevant_pools:
            # 嘗試通過 CoinGecko 獲取基本信息
            return _get_staking_info_from_coingecko(symbol)

        # 按 TVL 排序，優先顯示高 TVL 池
        relevant_pools.sort(key=lambda x: x["tvl"], reverse=True)

        # 取前 5 個池
        top_pools = relevant_pools[:5]

        result = f"## 💰 {symbol} Staking/Lending Yields\n\n"
        result += "| Protocol | Chain | Type | APY | TVL |\n"
        result += "|---|---|---|---|---|\n"

        for p in top_pools:
            tvl_str = (
                f"${p['tvl'] / 1_000_000:.1f}M"
                if p["tvl"] > 1_000_000
                else f"${p['tvl'] / 1_000:.0f}K"
            )
            result += f"| {p['project']} | {p['chain']} | {p['type']} | {p['apy']:.2f}% | {tvl_str} |\n"

        result += "\n> ⚠️ Yields change with market conditions; past returns do not guarantee future results.\n"
        result += "> 📊 Data Source: DefiLlama Yields\n"

        set_cached_data(cache_key, result)
        return result

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error fetching staking yields: {str(e)}"


def _get_staking_info_from_coingecko(symbol: str) -> str:
    """從 CoinGecko 獲取代幣質押信息（備用方案）"""
    try:
        # 搜索代幣
        search_resp = httpx.get(
            f"https://api.coingecko.com/api/v3/search?query={symbol}", timeout=10
        )
        coins = search_resp.json().get("coins", [])

        if not coins:
            return f"No staking data found for {symbol}. Please check the token symbol."

        coin_id = coins[0]["id"]
        for c in coins:
            if c.get("symbol", "").upper() == symbol:
                coin_id = c["id"]
                break

        # 獲取代幣詳細信息
        detail_resp = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&market_data=true",
            timeout=10,
        )

        if detail_resp.status_code != 200:
            return f"Unable to fetch detailed info for {symbol}."

        data = detail_resp.json()

        # 檢查是否有質押信息
        # CoinGecko 不直接提供質押 APY，返回基本信息
        result = f"## {symbol} Staking Info\n\n"

        # 檢查共識機制
        categories = data.get("categories", [])
        is_pos = any(
            "proof-of-stake" in c.lower() or "pos" in c.lower() for c in categories
        )
        is_pow = any(
            "proof-of-work" in c.lower() or "pow" in c.lower() for c in categories
        )

        if is_pow or symbol.upper() in ["BTC", "DOGE", "LTC", "BCH"]:
            result += f"❌ {symbol} uses Proof-of-Work (PoW) and does not support native staking.\n\n"
            result += "💡 **Alternatives**:\n"
            result += "- Earn yields through exchange products (e.g., Binance Earn)\n"
            result += "- Provide liquidity via lending protocols (e.g., Aave, Compound)\n"
        elif is_pos or symbol.upper() in [
            "ETH",
            "SOL",
            "ADA",
            "ATOM",
            "DOT",
            "MATIC",
            "AVAX",
            "NEAR",
            "SUI",
        ]:
            result += f"✅ {symbol} supports native staking.\n\n"
            result += "📊 **Suggestion**: check live staking yields on DefiLlama or the official wallet.\n"
            result += f"- Chain: {data.get('asset_platform_id', 'Unknown')}\n"
        else:
            result += f"ℹ️ Unable to determine {symbol}'s staking support status.\n"
            result += "Please check the project's official documentation for staking options.\n"

        return result

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error fetching staking info: {str(e)}"
