"""EVM 轉帳與餘額來源。

三條路（2026-09-12 加 Blockscout 後，沒 key 也抓得到原生轉帳與全部代幣）：
- 有 Etherscan V2 key（服務級 ``ETHERSCAN_SERVICE_API_KEY``）→ ``txlist``（原生幣）＋
  ``tokentx``（ERC-20），任何 chainid 都行。
- 沒 key → 同一組 Etherscan 相容 API 打 **Blockscout** 公開實例（``base.blockscout.com`` 等，
  免金鑰），一樣拿到原生＋ERC-20。
- Blockscout 也失敗 → ``EVM_RPC_URL`` 的 ``eth_getLogs`` 只抓 ERC-20（最後退路）。

餘額：Blockscout v2 ``token-balances``（含 USD 匯率，全部代幣）優先；退路是
``eth_getBalance``＋USDC ``balanceOf``。全部同步 httpx（cron 與 run_sync 呼叫端）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional

import httpx

from .transfers import Transfer

logger = logging.getLogger(__name__)

ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
RPC_TIMEOUT = 20.0
# chain label → (chain_id, native symbol, 平均出塊秒數)
CHAINS: Dict[str, tuple[int, str, float]] = {
    "base": (8453, "ETH", 2.0),
    "ethereum": (1, "ETH", 12.0),
    "arbitrum": (42161, "ETH", 0.25),
    "optimism": (10, "ETH", 2.0),
    "polygon": (137, "POL", 2.0),
}
# 已知代幣 (chain, contract lower) → (symbol, decimals)：省一次 eth_call，也給沒 symbol() 的合約用
KNOWN_TOKENS: Dict[tuple[str, str], tuple[str, int]] = {
    ("base", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): ("USDC", 6),
    ("base", "0x4200000000000000000000000000000000000006"): ("WETH", 18),
    ("base", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"): ("cbBTC", 8),
    ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): ("USDC", 6),
    ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7"): ("USDT", 6),
}
_META_CACHE: Dict[tuple[str, str], tuple[str, int]] = {}
# Blockscout 公開實例（Etherscan 相容 v1 API 在 /api，v2 在 /api/v2）；env 可覆蓋平台鏈那條
BLOCKSCOUT_URLS: Dict[str, str] = {
    "base": "https://base.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
}
# 餘額最多列幾個代幣（Blockscout 會把空投垃圾幣全列出來；有匯率的排前面）
MAX_TOKEN_BALANCES = 12


def rpc_url() -> str:
    from core.config import EVM_RPC_URL

    return EVM_RPC_URL


def platform_chain() -> str:
    """平台 RPC 對應的鏈（USDC 收款在 Base）。"""
    return os.getenv("ONCHAIN_EVM_CHAIN", "base").strip().lower() or "base"


def usdc_contract() -> Optional[str]:
    from core.config import EVM_USDC_CONTRACT

    return (EVM_USDC_CONTRACT or "").strip().lower() or None


def scan_blocks() -> int:
    return int(os.getenv("EVM_LOG_SCAN_BLOCKS", "2000"))


def blockscout_base(chain: str) -> Optional[str]:
    """該鏈的 Blockscout 根網址；``ONCHAIN_BLOCKSCOUT_URL`` 只覆蓋平台鏈那條（空字串＝停用）。"""
    if chain == platform_chain() and "ONCHAIN_BLOCKSCOUT_URL" in os.environ:
        return os.environ["ONCHAIN_BLOCKSCOUT_URL"].strip().rstrip("/") or None
    return BLOCKSCOUT_URLS.get(chain)


def _rpc(method: str, params: list, *, url: Optional[str] = None) -> Any:
    resp = httpx.post(
        url or rpc_url(),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=RPC_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(
            f"rpc {method}: {body['error'].get('message', body['error'])}"
        )
    return body.get("result")


def _hex_to_int(value: Any) -> int:
    if value in (None, "", "0x"):
        return 0
    return int(str(value), 16)


def _pad_address(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def _topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:]


def _decode_string(hexdata: str) -> str:
    """eth_call symbol() 回傳：ABI string，或舊合約的 bytes32。"""
    raw = bytes.fromhex(hexdata[2:]) if hexdata and hexdata != "0x" else b""
    if len(raw) >= 64 and int.from_bytes(raw[:32], "big") == 32:
        length = int.from_bytes(raw[32:64], "big")
        return raw[64 : 64 + length].decode("utf-8", "ignore").strip("\x00")
    return raw[:32].decode("utf-8", "ignore").strip("\x00")


def token_meta(
    chain: str, contract: str, *, url: Optional[str] = None
) -> Optional[tuple[str, int]]:
    """(symbol, decimals)；已知表 → 快取 → eth_call。查不到回 None（略過該代幣）。"""
    key = (chain, contract.lower())
    if key in KNOWN_TOKENS:
        return KNOWN_TOKENS[key]
    if key in _META_CACHE:
        return _META_CACHE[key]
    try:
        sym_hex = _rpc(
            "eth_call", [{"to": contract, "data": "0x95d89b41"}, "latest"], url=url
        )
        dec_hex = _rpc(
            "eth_call", [{"to": contract, "data": "0x313ce567"}, "latest"], url=url
        )
        symbol = _decode_string(sym_hex)
        decimals = _hex_to_int(dec_hex)
    except Exception as exc:  # noqa: BLE001 — 合約沒實作就跳過
        logger.debug(
            "[evm] token meta failed %s: %s", contract[:10], type(exc).__name__
        )
        return None
    if not symbol or decimals > 36:
        return None
    _META_CACHE[key] = (symbol, decimals)
    return _META_CACHE[key]


# ── 餘額 ─────────────────────────────────────────────────────────────────────


def fetch_blockscout_token_balances(
    address: str, *, chain: str
) -> Optional[List[Dict[str, Any]]]:
    """Blockscout v2 ``token-balances`` → [{symbol, amount, contract, usd, verified}]；失敗回 None。

    只留 ERC-20；有匯率的排前面（空投垃圾幣沒匯率），最多 MAX_TOKEN_BALANCES 個。
    """
    base = blockscout_base(chain)
    if not base:
        return None
    try:
        resp = httpx.get(
            f"{base}/api/v2/addresses/{address}/token-balances", timeout=RPC_TIMEOUT
        )
        if resp.status_code == 404:
            return []  # 地址沒任何代幣
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[evm] blockscout balances failed %s: %s", address[:10], type(exc).__name__
        )
        return None
    out: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        token = row.get("token") or {}
        if token.get("type") != "ERC-20":
            continue
        symbol = str(token.get("symbol") or "").strip()
        try:
            decimals = int(token.get("decimals") or 18)
            amount = int(str(row.get("value") or "0")) / 10**decimals
        except (TypeError, ValueError):
            continue
        if not symbol or amount <= 0:
            continue
        rate = token.get("exchange_rate")
        usd = amount * float(rate) if rate not in (None, "") else None
        # 同名假幣會被對到真幣價（實測 Coinbase 錢包一枚假 SAND 算出 146 億美元）：
        # 持有估值不可能超過該幣流通市值，超過就當沒價
        mcap = token.get("circulating_market_cap")
        if usd is not None and mcap not in (None, "") and usd > float(mcap):
            usd = None
        out.append(
            {
                "symbol": symbol,
                "amount": amount,
                "contract": (token.get("address") or "").lower() or None,
                "usd": round(usd, 2) if usd is not None else None,
                "verified": usd is not None,
            }
        )
    out.sort(key=lambda a: (a["usd"] is None, -(a["usd"] or 0), -a["amount"]))
    return out[:MAX_TOKEN_BALANCES]


def fetch_evm_balances(
    address: str,
    *,
    chain: Optional[str] = None,
    extra_contracts: Iterable[str] = (),
    url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """[{symbol, amount, contract, usd?}]：原生幣（RPC）＋代幣（Blockscout 全部；退路 USDC balanceOf）。"""
    chain = chain or platform_chain()
    _, native, _ = CHAINS.get(chain, (0, "ETH", 2.0))
    out: List[Dict[str, Any]] = []
    try:
        wei = _hex_to_int(_rpc("eth_getBalance", [address, "latest"], url=url))
        out.append({"symbol": native, "amount": wei / 10**18, "contract": None})
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[evm] native balance failed %s: %s", address[:10], type(exc).__name__
        )
    tokens = fetch_blockscout_token_balances(address, chain=chain)
    if tokens is not None:
        return out + tokens
    contracts = [c for c in [usdc_contract(), *extra_contracts] if c]
    for contract in dict.fromkeys(c.lower() for c in contracts):
        meta = token_meta(chain, contract, url=url)
        if not meta:
            continue
        symbol, decimals = meta
        try:
            data = "0x70a08231" + _pad_address(address)[2:]
            raw = _hex_to_int(
                _rpc("eth_call", [{"to": contract, "data": data}, "latest"], url=url)
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[evm] balanceOf failed %s: %s", contract[:10], type(exc).__name__
            )
            continue
        out.append(
            {"symbol": symbol, "amount": raw / 10**decimals, "contract": contract}
        )
    return out


# ── 轉帳：RPC logs（ERC-20） ──────────────────────────────────────────────────


def _estimate_from_block(
    latest: int, lookback_days: float, block_seconds: float
) -> int:
    return max(0, latest - int(lookback_days * 86400 / block_seconds))


def _get_logs(params: dict, *, url: Optional[str]) -> List[dict]:
    return _rpc("eth_getLogs", [params], url=url) or []


def fetch_erc20_transfers_via_logs(
    address: str,
    *,
    chain: Optional[str] = None,
    lookback_days: float = 1.0,
    url: Optional[str] = None,
    now_ts: Optional[int] = None,
) -> List[Transfer]:
    """自己是 from 或 to 的 ERC-20 Transfer log → Transfer。時間戳用區塊時間（每個區塊查一次、快取）。"""
    chain = chain or platform_chain()
    _, native, block_seconds = CHAINS.get(chain, (0, "ETH", 2.0))
    latest = _hex_to_int(_rpc("eth_blockNumber", [], url=url))
    start = _estimate_from_block(latest, lookback_days, block_seconds)
    topic_addr = _pad_address(address)
    logs: List[dict] = []
    step = scan_blocks()
    frm = start
    while frm <= latest:
        to = min(latest, frm + step - 1)
        base = {"fromBlock": hex(frm), "toBlock": hex(to)}
        try:
            logs += _get_logs(
                {**base, "topics": [ERC20_TRANSFER_TOPIC, topic_addr]}, url=url
            )
            logs += _get_logs(
                {**base, "topics": [ERC20_TRANSFER_TOPIC, None, topic_addr]}, url=url
            )
        except Exception as exc:  # noqa: BLE001 — 區段太大就對半再試一次
            if step > 250:
                step //= 2
                logger.info(
                    "[evm] getLogs failed, halving window to %d: %s",
                    step,
                    type(exc).__name__,
                )
                continue
            raise
        frm = to + 1
    block_ts: Dict[str, int] = {}
    out: List[Transfer] = []
    seen: set = set()
    for lg in logs:
        topics = lg.get("topics") or []
        if len(topics) < 3 or lg.get("removed"):
            continue
        key = (lg.get("transactionHash"), lg.get("logIndex"))
        if key in seen:
            continue
        seen.add(key)
        sender, recipient = _topic_to_address(topics[1]), _topic_to_address(topics[2])
        me = address.lower()
        if sender == me and recipient == me:
            continue
        direction = "out" if sender == me else "in"
        contract = (lg.get("address") or "").lower()
        meta = token_meta(chain, contract, url=url)
        if not meta:
            continue
        symbol, decimals = meta
        blk = lg.get("blockNumber")
        if blk not in block_ts:
            try:
                header = _rpc("eth_getBlockByNumber", [blk, False], url=url) or {}
                block_ts[blk] = _hex_to_int(header.get("timestamp"))
            except Exception:  # noqa: BLE001
                block_ts[blk] = now_ts or int(time.time())
        out.append(
            Transfer(
                chain=chain,
                tx_hash=str(lg.get("transactionHash") or ""),
                timestamp=block_ts[blk],
                direction=direction,
                symbol=symbol,
                amount=_hex_to_int(lg.get("data")) / 10**decimals,
                counterparty=recipient if direction == "out" else sender,
                contract=contract,
                native_symbol=native,
            )
        )
    return out


# ── 轉帳：Etherscan V2（原生＋ERC-20） ────────────────────────────────────────


def _etherscan(
    chain_id: int, params: dict, api_key: str, *, endpoint: str = ETHERSCAN_V2
) -> List[dict]:
    """Etherscan V2 或相容端點（Blockscout ``/api`` 忽略 chainid，沒 key 就不送 apikey）。"""
    query = {"chainid": chain_id, **params}
    if api_key:
        query["apikey"] = api_key
    resp = httpx.get(endpoint, params=query, timeout=RPC_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if str(body.get("status")) != "1":
        # "No transactions found" 也是 status 0
        return []
    return body.get("result") or []


def fetch_evm_transfers_via_etherscan(
    address: str,
    *,
    chain: str,
    api_key: str,
    since_ts: int,
    limit: int = 200,
    endpoint: str = ETHERSCAN_V2,
) -> List[Transfer]:
    chain_id, native, _ = CHAINS[chain]
    me = address.lower()
    out: List[Transfer] = []
    common = {
        "module": "account",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "desc",
    }
    for tx in _etherscan(
        chain_id, {**common, "action": "txlist"}, api_key, endpoint=endpoint
    ):
        ts = int(tx.get("timeStamp") or 0)
        value = int(tx.get("value") or 0)
        if ts < since_ts or value <= 0 or str(tx.get("isError")) == "1":
            continue
        sender, recipient = (tx.get("from") or "").lower(), (tx.get("to") or "").lower()
        if sender == me and recipient == me:
            continue
        direction = "out" if sender == me else "in"
        gas = (
            int(tx.get("gasUsed") or 0) * int(tx.get("gasPrice") or 0)
            if direction == "out"
            else 0
        )
        out.append(
            Transfer(
                chain=chain,
                tx_hash=str(tx.get("hash") or ""),
                timestamp=ts,
                direction=direction,
                symbol=native,
                amount=value / 10**18,
                counterparty=recipient if direction == "out" else sender,
                fee_native=gas / 10**18,
                native_symbol=native,
            )
        )
    for tx in _etherscan(
        chain_id, {**common, "action": "tokentx"}, api_key, endpoint=endpoint
    ):
        ts = int(tx.get("timeStamp") or 0)
        if ts < since_ts:
            continue
        sender, recipient = (tx.get("from") or "").lower(), (tx.get("to") or "").lower()
        if sender == me and recipient == me:
            continue
        decimals = int(tx.get("tokenDecimal") or 0)
        symbol = str(tx.get("tokenSymbol") or "").strip()
        if not symbol:
            continue
        direction = "out" if sender == me else "in"
        out.append(
            Transfer(
                chain=chain,
                tx_hash=str(tx.get("hash") or ""),
                timestamp=ts,
                direction=direction,
                symbol=symbol,
                amount=int(tx.get("value") or 0) / 10**decimals,
                counterparty=recipient if direction == "out" else sender,
                contract=(tx.get("contractAddress") or "").lower() or None,
                native_symbol=native,
            )
        )
    return out


def fetch_evm_transfers(
    address: str,
    *,
    since_ts: int,
    lookback_days: float,
    chains: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    url: Optional[str] = None,
) -> tuple[List[Transfer], List[str]]:
    """回 (transfers, notes)。有 key 走 Etherscan（多鏈）；沒有走 Blockscout（免金鑰，
    原生＋ERC-20）；Blockscout 也失敗才退 RPC logs（只有 ERC-20）。"""
    api_key = (
        api_key
        if api_key is not None
        else os.getenv("ETHERSCAN_SERVICE_API_KEY", "").strip()
    )
    notes: List[str] = []
    if api_key:
        result: List[Transfer] = []
        for chain in chains or [platform_chain()]:
            if chain not in CHAINS:
                continue
            try:
                result += fetch_evm_transfers_via_etherscan(
                    address, chain=chain, api_key=api_key, since_ts=since_ts
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"etherscan:{chain}:{type(exc).__name__}")
        return result, notes
    chain = platform_chain()
    base = blockscout_base(chain) if chain in CHAINS else None
    if base:
        try:
            return (
                fetch_evm_transfers_via_etherscan(
                    address,
                    chain=chain,
                    api_key="",
                    since_ts=since_ts,
                    endpoint=f"{base}/api",
                ),
                ["blockscout"],
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"blockscout:{type(exc).__name__}")
    try:
        return (
            fetch_erc20_transfers_via_logs(
                address, lookback_days=lookback_days, url=url
            ),
            notes + ["rpc_logs_only:native_transfers_not_included"],
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"rpc:{type(exc).__name__}")
        return [], notes
