"""
Etherscan 工具
ETH Balance, ERC20 Balance, Address Transactions, Contract Info, ETH Price

設計原則：
- Etherscan API 採強制 BYOK（使用者自帶 API 金鑰）。
- 使用者於「工具設定」填入自己的 Etherscan 金鑰後，工具會直接呼叫 API 回傳資料。
- 未設定金鑰時，回傳引導訊息（前往 Etherscan 網站自行查詢）。
- 多鏈支援（docs/plans/2026-08-11-evm-multichain-scam-detection-design.md）：
  Etherscan V2 endpoint 原生支援多鏈（chainid 參數）。工具簽名暴露 chain_id，
  預設 1（Ethereum mainnet）。agent 透過 system prompt 學會在使用者提及
  BSC/Polygon/Arbitrum 等鏈時傳正確 chain_id。
"""

import asyncio

import httpx
from langchain_core.tools import tool

_ETHERSCAN_ENDPOINT = "https://api.etherscan.io/v2/api"
_WEI_PER_ETH = 10**18

# 鏈名（大小寫不拘）→ Etherscan V2 chainid。未知鏈名回 1（Ethereum，安全預設）。
# 與 goplus.py 的 _CHAIN_NAMES（id→label）互補——本表是反向 name→id。
_CHAIN_NAME_TO_ID = {
    "ethereum": 1, "eth": 1, "mainnet": 1,
    "bsc": 56, "binance": 56, "bnb": 56, "binance smart chain": 56,
    "arbitrum": 42161, "arb": 42161, "arbitrum one": 42161,
    "polygon": 137, "matic": 137, "polygon pos": 137,
    "optimism": 10, "op": 10, "optimistic": 10,
    "base": 8453,
    "avalanche": 43114, "avax": 43114, "avalanche c-chain": 43114,
    "fantom": 250, "ftm": 250,
    "zksync": 324, "zksync era": 324,
    "scroll": 534352,
    "linea": 59144,
}

# chainid → explorer 基底 URL（給無 key 時的引導連結用）。
_EXPLORER_BASES = {
    1: "etherscan.io",
    56: "bscscan.com",
    42161: "arbiscan.io",
    137: "polygonscan.com",
    10: "optimistic.etherscan.io",
    8453: "basescan.org",
    43114: "snowtrace.io",
    250: "ftmscan.com",
    324: "era.zksync.network",
    534352: "scrollscan.com",
    59144: "lineascan.build",
}


def resolve_chain_id(name: str) -> int:
    """鏈名（大小寫不拘）→ chain_id；未知回 1（Ethereum，安全預設）。

    也接受純數字字串（直接當 chain_id）。
    """
    if not name:
        return 1
    s = str(name).strip().lower()
    if s.isdigit():
        return int(s)
    return _CHAIN_NAME_TO_ID.get(s, 1)


def _explorer_base(chain_id: int) -> str:
    """chainid → explorer 基底 URL；未知回 etherscan.io。"""
    return _EXPLORER_BASES.get(chain_id, "etherscan.io")


def _chain_label(chain_id: int) -> str:
    """chainid → 人類可讀鏈名（給回應標題用）。"""
    _id_to_label = {v: k for k, v in _CHAIN_NAME_TO_ID.items() if v not in (1,)}
    _id_to_label[1] = "ethereum"
    label = _id_to_label.get(chain_id, f"chain {chain_id}")
    # 取主名（第一個 alias 的主詞）
    return label.replace(" smart chain", "").replace(" one", "").replace(" pos", "").title()


def _user_etherscan_key() -> str | None:
    """取得使用者自帶的 Etherscan 金鑰（BYOK，無官方 fallback）。"""
    from core.tools.key_resolver import resolve_tool_key

    return resolve_tool_key("etherscan", official_env=None)


def _etherscan_get(params: dict, api_key: str, chain_id: int = 1) -> dict:
    """呼叫 Etherscan API V2 並回傳 JSON。chain_id 預設 1（Ethereum mainnet）。"""
    query = {"chainid": chain_id, **params, "apikey": api_key}
    resp = httpx.get(_ETHERSCAN_ENDPOINT, params=query, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def _is_invalid_key(data: dict) -> bool:
    """判斷回應是否為金鑰無效/權限不足。"""
    msg = str(data.get("result", "")) + str(data.get("message", ""))
    return "Invalid API Key" in msg or "rate limit" in msg.lower()


@tool
def get_eth_balance(address: str, chain_id: int = 1) -> str:
    """查詢 EVM 地址的原生代幣餘額（Ethereum/BSC/Polygon/Arbitrum 等，需自帶 Etherscan 金鑰）。

    chain_id 預設 1（Ethereum mainnet）。其他鏈：BSC=56, Polygon=137, Arbitrum=42161,
    Optimism=10, Base=8453, Avalanche=43114, Fantom=250, zkSync=324, Scroll=534352, Linea=59144。
    """
    if not address.startswith("0x") or len(address) != 42:
        return "❌ Invalid Ethereum address format. The address must start with 0x and be 42 characters long."

    api_key = _user_etherscan_key()
    explorer = _explorer_base(chain_id)
    if api_key:
        try:
            data = _etherscan_get(
                {
                    "module": "account",
                    "action": "balance",
                    "address": address,
                    "tag": "latest",
                },
                api_key,
                chain_id=chain_id,
            )
            if _is_invalid_key(data):
                return "❌ Etherscan API key is invalid or rate-limited. Please check your API key."
            if data.get("status") == "1":
                eth = int(data["result"]) / _WEI_PER_ETH
                return f"""## 💰 Native Balance

**Address**: `{address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})
**Balance**: {eth:,.6f}

🔗 [View on explorer](https://{explorer}/address/{address})

*(Source: Etherscan API V2)*"""
            return f"❌ Etherscan query failed: {data.get('message', 'unknown error')}"
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            return f"❌ Etherscan query failed: {e}"

    return f"""## 💰 Balance Query

**Address**: `{address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})

> 💡 **Check it on the block explorer**:
>
> 🔗 [View balance here](https://{explorer}/address/{address})
>
> The explorer provides full address information:
> - Native balance
> - ERC20 token balances
> - NFT holdings
> - Transaction history
> - Contract interaction records

> 💡 **Want a direct query?** Add your Etherscan key in the tool settings
> (free sign-up: https://{explorer}/myapikey) so the AI can read the balance directly."""


@tool
def get_erc20_token_balance(address: str, contract_address: str, chain_id: int = 1) -> str:
    """查詢 EVM 地址的 ERC20 代幣餘額（Ethereum/BSC/Polygon/Arbitrum 等，需自帶 Etherscan 金鑰）。

    chain_id 預設 1（Ethereum mainnet）。其他鏈：BSC=56, Polygon=137, Arbitrum=42161 等。
    """
    if not address.startswith("0x") or len(address) != 42:
        return "❌ Invalid wallet address format"
    if not contract_address.startswith("0x") or len(contract_address) != 42:
        return "❌ Invalid contract address format"

    api_key = _user_etherscan_key()
    explorer = _explorer_base(chain_id)
    if api_key:
        try:
            data = _etherscan_get(
                {
                    "module": "account",
                    "action": "tokenbalance",
                    "contractaddress": contract_address,
                    "address": address,
                    "tag": "latest",
                },
                api_key,
                chain_id=chain_id,
            )
            if _is_invalid_key(data):
                return "❌ Etherscan API key is invalid or rate-limited. Please check your API key."
            if data.get("status") == "1":
                raw = data["result"]
                return f"""## 🪙 ERC20 Token Balance

**Wallet Address**: `{address}`
**Token Contract**: `{contract_address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})
**Raw Balance**: {raw}

> ⚠️ The value above is in the smallest unit; convert it using the token's decimals.
🔗 [View on explorer](https://{explorer}/token/{contract_address}?a={address})

*(Source: Etherscan API V2)*"""
            return f"❌ Etherscan query failed: {data.get('message', 'unknown error')}"
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            return f"❌ Etherscan query failed: {e}"

    return f"""## 🪙 ERC20 Token Balance Query

**Wallet Address**: `{address}`
**Token Contract**: `{contract_address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})

> 💡 **Check it on the block explorer**:
>
> 🔗 [View token balance here](https://{explorer}/token/{contract_address}?a={address})
>
> Want the AI to read it directly? Add your Etherscan key in the tool settings
> (free sign-up: https://{explorer}/myapikey)."""


@tool
def get_address_transactions(address: str, limit: int = 10, chain_id: int = 1) -> str:
    """查詢 EVM 地址的最近交易記錄（Ethereum/BSC/Polygon/Arbitrum 等，需自帶 Etherscan 金鑰）。

    chain_id 預設 1（Ethereum mainnet）。其他鏈：BSC=56, Polygon=137, Arbitrum=42161 等。
    """
    if not address.startswith("0x") or len(address) != 42:
        return "❌ Invalid Ethereum address format"

    limit = max(1, min(limit, 50))
    api_key = _user_etherscan_key()
    explorer = _explorer_base(chain_id)
    if api_key:
        try:
            data = _etherscan_get(
                {
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": limit,
                    "sort": "desc",
                },
                api_key,
                chain_id=chain_id,
            )
            if _is_invalid_key(data):
                return "❌ Etherscan API key is invalid or rate-limited. Please check your API key."
            if data.get("status") == "1":
                rows = ["| Hash | Direction | Value | Block |", "|---|---|---|---|"]
                for tx in data.get("result", [])[:limit]:
                    direction = (
                        "Sent"
                        if tx.get("from", "").lower() == address.lower()
                        else "Received"
                    )
                    eth = int(tx.get("value", 0)) / _WEI_PER_ETH
                    h = tx.get("hash", "")
                    short = f"{h[:10]}…{h[-6:]}" if h else ""
                    rows.append(
                        f"| [{short}](https://{explorer}/tx/{h}) | {direction} | {eth:.6f} | {tx.get('blockNumber', '')} |"
                    )
                table = "\n".join(rows)
                return f"""## 📜 Latest {limit} Transactions

**Address**: `{address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})

{table}

*(Source: Etherscan API V2)*"""
            return f"❌ Etherscan query failed: {data.get('message', 'no transactions or unknown error')}"
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            return f"❌ Etherscan query failed: {e}"

    return f"""## 📜 Transaction History Query

**Address**: `{address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})

> 💡 **View transactions on the block explorer**:
>
> 🔗 [View transaction history here](https://{explorer}/address/{address})
>
> The explorer provides:
> - Full transaction history
> - ERC20 transfer records
> - Failed transaction details
> - Gas fee analysis

**Recommended advanced tools**:
| Tool | URL | Highlights |
|---|---|---|
| Explorer | [{explorer}](https://{explorer}) | Block explorer for {_chain_label(chain_id)} |
| Dune Analytics | [dune.com](https://dune.com) | SQL queries for transactions |
| Nansen | [nansen.ai](https://nansen.ai) | Smart address labels |"""


@tool
def get_contract_info(contract_address: str, chain_id: int = 1) -> str:
    """查詢 EVM 智能合約的基本資訊 - 引導用戶到區塊瀏覽器（支援多鏈）。

    chain_id 預設 1（Ethereum mainnet）。其他鏈：BSC=56, Polygon=137, Arbitrum=42161 等。
    """
    if not contract_address.startswith("0x") or len(contract_address) != 42:
        return "❌ Invalid contract address format"

    explorer = _explorer_base(chain_id)
    return f"""## 📄 Smart Contract Information

**Contract Address**: `{contract_address}`
**Chain**: {_chain_label(chain_id)} (chain_id={chain_id})

> 💡 **View the contract on the block explorer**:
>
> 🔗 [View contract details here](https://{explorer}/address/{contract_address})
>
> The explorer provides:
> - Contract creator
> - Creation transaction
> - Contract code (if verified)
> - Read/write contract functions
> - Event logs

**Safety tips**:
⚠️ Before interacting with an unknown contract, check:
1. Whether the contract is verified
2. Whether it passed a security audit
3. Community reviews"""


@tool
def get_eth_price_from_etherscan() -> str:
    """獲取 ETH 即時價格 - 使用免費 API"""
    import httpx

    try:
        # 使用 CoinGecko 免費 API（無需 Key）
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd,btc&include_24hr_change=true",
            timeout=10,
        )

        if resp.status_code == 200:
            data = resp.json().get("ethereum", {})
            usd = data.get("usd", "N/A")
            btc = data.get("btc", "N/A")
            change = data.get("usd_24h_change", 0)

            change_emoji = "📈" if change > 0 else "📉"
            return f"""## 💎 ETH Live Price

| Asset | Price |
|---|---|
| USD | ${usd:,.2f} |
| BTC | ₿{btc:.6f} |

{change_emoji} **24h Change**: {change:+.2f}%

*(Source: CoinGecko)*"""

        return "Unable to fetch the ETH price. Please try again later."
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Query failed: {str(e)}"
