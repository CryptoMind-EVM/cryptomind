"""
鏈上數據工具
設計原則：公開平台工具不依賴個人 API Key

可用的免費 API：
- Etherscan Gas Oracle（無需 Key）
- Blockchain.info（無需 Key）
- CoinGecko（無需 Key）
- DefiLlama（無需 Key）

需要付費/Key 的功能（改為引導用戶到專業工具）：
- ETH 鯨魚交易 -> Whale Alert, Etherscan
- Exchange Flow -> CryptoQuant, Glassnode
"""

import asyncio

import httpx
from langchain_core.tools import tool

from .common import get_cached_data, set_cached_data


@tool
def get_gas_fees() -> str:
    """獲取 Ethereum 網路的即時 Gas 費用"""
    try:
        # 使用 Blocknative 免費 API（無需 Key）
        resp = httpx.get(
            "https://api.blocknative.com/gasprices/blockprices", timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            block_prices = data.get("blockPrices", [])
            if block_prices:
                prices = block_prices[0].get("estimatedPrices", [])

                # 按 confidence 排序
                prices_dict = {p["confidence"]: p for p in prices}

                # 99% = 快, 80% = 標準, 70% = 慢
                fast = prices_dict.get(99, {}).get("price", "N/A")
                standard = prices_dict.get(80, {}).get("price", "N/A")
                slow = prices_dict.get(70, {}).get("price", "N/A")
                base_fee = block_prices[0].get("baseFeePerGas", 0)

                return f"""## ⛽ Ethereum Gas Fees

| Level | Gwei | Use case |
|---|---|---|
| 🐢 Slow | {slow} | No rush (70% confidence) |
| 🚗 Standard | {standard} | General (80% confidence) |
| 🚀 Fast | {fast} | Time-sensitive (99% confidence) |

- **Base Fee**: {base_fee:.4f} Gwei

*(Source: Blocknative)*"""
        return "Unable to fetch gas data."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_whale_transactions(symbol: str = "BTC", min_value_usd: int = 500000) -> str:
    """獲取大額鏈上轉帳（鯨魚交易）。

    自動根據代幣類型選擇對應的區塊瀏覽器 API。
    支援任何有公開區塊瀏覽器的代幣。
    """
    symbol = symbol.upper()
    cache_key = f"whale_tx_{symbol}_{min_value_usd}"
    cached = get_cached_data(cache_key, 120)
    if cached:
        return cached

    try:
        # 1. 先獲取代幣資訊（包括所在鏈）
        token_info = _get_token_chain_info(symbol)

        if not token_info:
            return f"No info found for {symbol}. Please check that the token symbol is correct."

        chain = token_info.get("chain", "unknown")
        token_price = token_info.get("price", 0)

        # 2. 根據鏈類型調用對應的區塊瀏覽器 API
        if chain == "bitcoin" or symbol == "BTC":
            result = _fetch_btc_whale_tx(min_value_usd)
        elif chain == "ethereum" or symbol == "ETH":
            result = _fetch_eth_whale_tx(min_value_usd)
        elif chain in ["solana", "spl"]:
            result = _fetch_solana_whale_tx(symbol, min_value_usd, token_price)
        elif chain in ["bsc", "binance-smart-chain"]:
            result = _fetch_bsc_whale_tx(symbol, min_value_usd, token_price)
        else:
            result = _fetch_generic_whale_tx(symbol, chain, min_value_usd, token_info)

        if result:
            set_cached_data(cache_key, result)
            return result

        return f"Unable to fetch whale transaction data for {symbol}."

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"


def _get_token_chain_info(symbol: str) -> dict:
    """從 CoinGecko 獲取代幣所在鏈資訊"""
    try:
        # 搜索代幣
        search_resp = httpx.get(
            f"https://api.coingecko.com/api/v3/search?query={symbol}", timeout=10
        )
        if search_resp.status_code != 200:
            return None

        coins = search_resp.json().get("coins", [])
        if not coins:
            return None

        # 找到匹配的代幣
        coin_id = None
        for c in coins:
            if c.get("symbol", "").upper() == symbol:
                coin_id = c["id"]
                break
        if not coin_id:
            coin_id = coins[0]["id"]

        # 獲取詳細資訊
        detail_resp = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&market_data=true",
            timeout=10,
        )
        if detail_resp.status_code != 200:
            return None

        detail = detail_resp.json()
        platforms = detail.get("platforms", {})
        market_data = detail.get("market_data", {})

        # 確定主要鏈
        chain = "unknown"
        if symbol == "BTC":
            chain = "bitcoin"
        elif symbol == "ETH" or "ethereum" in platforms:
            chain = "ethereum"
        elif "solana" in platforms:
            chain = "solana"
        elif "binance-smart-chain" in platforms:
            chain = "bsc"

        return {
            "id": coin_id,
            "name": detail.get("name", symbol),
            "symbol": symbol,
            "chain": chain,
            "price": market_data.get("current_price", {}).get("usd", 0),
            "platforms": platforms,
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return {"chain": "unknown", "price": 0}


def _get_current_price(symbol: str) -> float:
    """獲取代幣當前價格"""
    try:
        resp = httpx.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=usd",
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if symbol.lower() in data:
                return data[symbol.lower()].get("usd", 0)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        pass

    # 嘗試通過搜索獲取
    info = _get_token_chain_info(symbol)
    return info.get("price", 0) if info else 0


def _fetch_btc_whale_tx(min_value_usd: int) -> str:
    """獲取 BTC 鯨魚交易"""
    try:
        # 使用 blockchain.info API（正確的端點）
        resp = httpx.get(
            "https://blockchain.info/unconfirmed-transactions?format=json", timeout=15
        )
        if resp.status_code != 200:
            return None

        txs = resp.json().get("txs", [])[:50]
        btc_price = _get_current_price("bitcoin") or 85000
        whale_txs = []

        for tx in txs:
            for out in tx.get("out", []):
                value_satoshi = out.get("value", 0)
                if value_satoshi > 0:
                    value_btc = value_satoshi / 100_000_000
                    value_usd = value_btc * btc_price
                    if value_usd >= min_value_usd:
                        whale_txs.append(
                            {
                                "hash": tx.get("hash", "")[:16] + "...",
                                "value_btc": value_btc,
                                "value_usd": value_usd,
                            }
                        )

        if not whale_txs:
            return f"## 🐋 BTC Whale Transactions\n\nNo transfers >{min_value_usd / 1_000_000:.1f}M USD found recently."

        whale_txs.sort(key=lambda x: x["value_usd"], reverse=True)

        result = f"## 🐋 BTC Whale Transactions (>{min_value_usd / 1_000_000:.1f}M USD)\n\n"
        for i, tx in enumerate(whale_txs[:5], 1):
            result += f"{i}. **{tx['value_btc']:.4f} BTC** (~${tx['value_usd'] / 1_000_000:.1f}M)\n"
        result += f"\n*(Source: Blockchain.com | BTC: ${btc_price:,.0f})*"
        return result

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def _fetch_eth_whale_tx(min_value_usd: int) -> str:
    """獲取 ETH 鯨魚交易 - 引導用戶到專業工具

    設計原則：公開平台工具不依賴個人 API Key
    """
    eth_price = _get_current_price("ethereum") or 2200
    min_eth = min_value_usd / eth_price

    return f"""## 🐋 ETH Whale Transaction Tracking

- **Threshold**: >{min_value_usd / 1_000_000:.1f}M USD (~{min_eth:.1f} ETH)
- **Current price**: ${eth_price:,.0f}

> 💡 **Professional on-chain tracking tools**:
>
> | Tool | URL | Highlights |
> |---|---|---|
> | **Whale Alert** | [whale-alert.io](https://whale-alert.io) | Cross-chain real-time tracking, free |
> | **Etherscan** | [etherscan.io/txs?f=hi](https://etherscan.io/txs?f=hi) | Filter large ETH transactions |
> | **Nansen** | [nansen.ai](https://nansen.ai) | Smart wallet labels |
> | **Arkham** | [arkhamintelligence.com](https://arkhamintelligence.com) | Entity address labels |

**Why not provide the data directly?**
On-chain tracking requires professional APIs (paid or key-required), and a public platform should not depend on personal keys.
Please use the professional tools above — they offer more complete data and features.

*(ETH price source: CoinGecko)*"""


def _fetch_solana_whale_tx(symbol: str, min_value_usd: int, token_price: float) -> str:
    """獲取 Solana 鏈上鯨魚交易 - 引導用戶到專業工具"""
    min_tokens = min_value_usd / token_price if token_price > 0 else 0

    return f"""## 🐋 {symbol} (Solana) Whale Transaction Tracking

- **Threshold**: >{min_value_usd / 1_000_000:.1f}M USD (~{min_tokens:,.0f} {symbol})
- **Current price**: ${token_price:,.4f}

> 💡 **Professional on-chain tracking tools**:
>
> | Tool | URL | Highlights |
> |---|---|---|
> | **Solscan** | [solscan.io](https://solscan.io) | Solana block explorer |
> | **Whale Alert** | [whale-alert.io](https://whale-alert.io) | Cross-chain real-time tracking |
> | **Solana FM** | [solana.fm](https://solana.fm) | Detailed transaction analysis |

*(Price source: CoinGecko)*"""


def _fetch_bsc_whale_tx(symbol: str, min_value_usd: int, token_price: float) -> str:
    """獲取 BSC 鏈上鯨魚交易 - 引導用戶到專業工具"""
    min_tokens = min_value_usd / token_price if token_price > 0 else 0

    return f"""## 🐋 {symbol} (BSC) Whale Transaction Tracking

- **Threshold**: >{min_value_usd / 1_000_000:.1f}M USD (~{min_tokens:,.0f} {symbol})
- **Current price**: ${token_price:,.6f}

> 💡 **Professional on-chain tracking tools**:
>
> | Tool | URL | Highlights |
> |---|---|---|
> | **BscScan** | [bscscan.com](https://bscscan.com) | BNB Chain block explorer |
> | **Whale Alert** | [whale-alert.io](https://whale-alert.io) | Cross-chain real-time tracking |
> | **Dexscreener** | [dexscreener.com](https://dexscreener.com) | DEX transaction tracking |

*(Price source: CoinGecko)*"""


def _fetch_generic_whale_tx(
    symbol: str, chain: str, min_value_usd: int, token_info: dict
) -> str:
    """通用鯨魚交易查詢（提供區塊瀏覽器連結）"""
    token_price = token_info.get("price", 0)
    platforms = token_info.get("platforms", {})
    min_tokens = min_value_usd / token_price if token_price > 0 else 0

    # 根據鏈提供對應的區塊瀏覽器
    explorers = {
        "ethereum": "https://etherscan.io",
        "solana": "https://solscan.io",
        "bsc": "https://bscscan.com",
        "polygon": "https://polygonscan.com",
        "arbitrum": "https://arbiscan.io",
        "avalanche": "https://snowtrace.io",
        "optimism": "https://optimistic.etherscan.io",
    }

    explorer_url = explorers.get(chain, "https://etherscan.io")

    # 獲取合約地址（如果有）
    contract_address = platforms.get(chain) or platforms.get("ethereum") or ""

    result = f"""## 🐋 {symbol} Whale Transactions

- **Chain**: {chain}
- **Threshold**: >{min_value_usd / 1_000_000:.1f}M USD (~{min_tokens:,.0f} {symbol})
- **Current price**: ${token_price:,.6f}
"""

    if contract_address:
        result += f"- **Contract address**: `{contract_address}`\n"

    result += f"""
> 💡 **View large transfers**:
> 1. Go to the [{chain.capitalize()} block explorer]({explorer_url})
> 2. Search for the {symbol} token{" (`" + contract_address + "`)" if contract_address else ""}
> 3. Check the "Transfers" tab

*(Data source: CoinGecko price)*"""

    return result


@tool
def get_exchange_flow(symbol: str = "BTC") -> str:
    """獲取交易所資金流向 - 引導用戶到專業工具

    注意：交易所流向數據需要付費 API，此工具提供專業網站引導。
    """
    symbol = symbol.upper()
    cache_key = f"exchange_flow_{symbol}"
    cached = get_cached_data(cache_key, 300)
    if cached:
        return cached

    try:
        # 嘗試從 CoinGecko 獲取基本資訊
        token_info = _get_token_chain_info(symbol)
        token_price = token_info.get("price", 0) if token_info else 0

        result = f"""## 🏦 {symbol} Exchange Fund Flow

- **Current price**: ${token_price:,.2f}

> 💡 **Professional exchange flow tools**:
>
> | Tool | URL | Highlights |
> |---|---|---|
> | **CoinGlass** | [coinglass.com](https://coinglass.com) | Futures/spot flows, liquidation maps |
> | **IntoTheBlock** | [intothablock.com](https://intothablock.com) | On-chain indicator analysis |
> | **Dune Analytics** | [dune.com](https://dune.com) | Community dashboards (free) |
> | **Glassnode** | [glassnode.com](https://glassnode.com) | Professional on-chain analysis (paid) |

**📊 How to read the indicators**:
| Direction | Meaning | Market impact |
|---|---|---|
| Net outflow ↑ | Withdrawals from exchanges | Less potential selling pressure = **bullish** |
| Net inflow ↑ | Deposits to exchanges | More potential selling pressure = **bearish** |

*(Price source: CoinGecko)*"""

        set_cached_data(cache_key, result)
        return result

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error: {str(e)}"
