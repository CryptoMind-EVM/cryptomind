"""
TON 鏈上錢包信任訊號採集（trust_score 的鏈上輸入）

問題
----
core/identity/trust.py 的 assess_identity_trust 需要兩個鏈上訊號才會離開 baseline：
- wallet_first_active：錢包首次活躍時間（年齡）
- wallet_tx_count：歷史交易數（活動度）

但既有工具 get_ton_balance 只回餘額與狀態，不回這兩個。本模組補上這個缺口。

設計（實測 + 業界共識）
----
- 業界標準（Chainalysis / TRM / EIP-11）：wallet age = **第一筆交易時間**。
  TonAPI /v2/accounts 只回 last_activity（最後活動），把 last 當 first 是 bug——
  會把「老錢包近期活躍」誤算成新錢包，懲罰忠誠用戶。
- 正確做法：打 /v2/blockchain/accounts/{addr}/transactions 用 before_lt 翻頁
  到最舊一筆，utime 即首次活動。
- **永久快取**：錢包年齡不變，翻頁只做一次，之後讀快取（Redis），避免高頻
  錢包每次重算都翻頁打爆 rate limit。
- graceful：任何訊號拿不到都不報錯，回 None（上層不計分）。
  翻頁設上限（10 頁）防高頻錢包爆 API，找不到最舊就回退 last_activity（保守）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from core.onchain.addresses import is_ton_address
from core.shared_cache import get_json, set_json

logger = logging.getLogger(__name__)

# TonAPI 公開 endpoint（與 get_ton_jetton_balances 同源）。
_TONAPI_ACCOUNT_URL = "https://tonapi.io/v2/accounts/{address}"
_TONAPI_TX_URL = "https://tonapi.io/v2/blockchain/accounts/{address}/transactions"

# 翻頁上限——超過此頁數仍找不到最舊就回退（防高頻錢包爆 API）。
_MAX_TX_PAGES = 10
# 每頁筆數。
_TX_PAGE_SIZE = 50
# 快取 key 前綴 + TTL（錢包年齡不變，30 天快取合理——幾乎永不重算）。
_FIRST_TX_CACHE_KEY = "trust:first_tx:{address}"
_FIRST_TX_CACHE_TTL = 30 * 24 * 3600  # 30 天


async def _fetch_first_tx_time(address: str) -> Optional[int]:
    """翻頁找最舊一筆交易的 utime（業界標準 wallet age 定義）。

    用 before_lt 翻頁到 next_from 為空（最舊），或達頁數上限。
    回 unix seconds 或 None（找不到 / 失敗 / 未初始化）。
    """
    # 先讀快取（錢包年齡不變，翻頁只做一次）
    cached = get_json(_FIRST_TX_CACHE_KEY.format(address=address))
    if cached is not None:
        return cached

    before_lt: Optional[str] = None
    oldest_utime: Optional[int] = None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(_MAX_TX_PAGES):
                params: dict = {"limit": _TX_PAGE_SIZE}
                if before_lt:
                    params["before_lt"] = before_lt
                resp = await client.get(
                    _TONAPI_TX_URL.format(address=address), params=params
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[onchain_signals] tx page failed %s -> HTTP %s",
                        address, resp.status_code,
                    )
                    break
                body = resp.json()
                txs = body.get("transactions", [])
                if not txs:
                    break
                oldest_utime = txs[-1].get("utime") or oldest_utime
                nxt = body.get("next_from")
                if not nxt:
                    break
                before_lt = nxt
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("[onchain_signals] tx fetch failed for %s: %s", address, exc)
        return None

    if oldest_utime:
        # 快取（永久——錢包年齡不變）
        set_json(_FIRST_TX_CACHE_KEY.format(address=address), oldest_utime, _FIRST_TX_CACHE_TTL)
        return oldest_utime
    return None


async def fetch_wallet_onchain_signals(address: str) -> dict:
    """採集單一 TON 錢包的鏈上年齡與活動度訊號。

    Args:
        address: TON 地址（friendly EQ／UQ… 或 raw 0:hex 皆可）。

    Returns:
        dict，可能含的 key：
            - first_active: Optional[datetime] — 錢包首次活躍時間（UTC，第一筆交易）
            - tx_count: Optional[int] — 歷史交易數（近似，v1 用「有活動」當訊號）
            - is_active: Optional[bool] — 錢包狀態是否 active
            - source: str — 訊號來源標記
            - first_tx_time: Optional[int] — 原始第一筆交易 utime（audit 用）
        任何訊號拿不到時該 key 為 None；完全失敗時回 {"error": ...}。
    """
    addr = (address or "").strip()
    if not is_ton_address(addr):
        return {"error": "Invalid TON address"}

    # 1. 帳號基本資訊（狀態 + 最後活動，作為 fallback 與 is_active）
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_TONAPI_ACCOUNT_URL.format(address=addr))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("[onchain_signals] TonAPI fetch failed for %s: %s", addr, exc)
        return {"error": "Unable to reach TON account API"}

    if resp.status_code == 404:
        # 未初始化 / 從未上鏈的錢包——不是錯誤，是「這個錢包還沒歷史」。
        return {"first_active": None, "tx_count": None, "is_active": False, "source": "tonapi"}
    if resp.status_code != 200:
        logger.warning("[onchain_signals] TonAPI %s -> HTTP %s", addr, resp.status_code)
        return {"error": f"TON account API HTTP {resp.status_code}"}

    body = resp.json()
    status = body.get("status", "unknown")
    is_active = status == "active"

    # 2. 第一筆交易時間（業界標準 wallet age）——翻頁 + 快取
    first_tx_time = await _fetch_first_tx_time(addr)

    # 3. fallback：找不到最舊（高頻錢包超頁數 / 無交易）→ 用 last_activity（保守）
    first_active: Optional[datetime] = None
    if first_tx_time is not None:
        first_active = datetime.fromtimestamp(first_tx_time, tz=timezone.utc)
    else:
        last_activity = body.get("last_activity")
        if isinstance(last_activity, int) and last_activity > 0:
            first_active = datetime.fromtimestamp(last_activity, tz=timezone.utc)
            logger.info(
                "[onchain_signals] %s 翻頁未找到最舊，回退 last_activity=%s",
                addr, last_activity,
            )

    # 交易數近似：有首次活動 = 至少有過活動。精確總數需翻全部頁（成本高），v1 用
    # 「曾活動」當活動度訊號（assess 的 _wallet_activity_score 對非零值計分）。
    tx_count: Optional[int] = 1 if first_active is not None else None

    return {
        "first_active": first_active,
        "tx_count": tx_count,
        "is_active": is_active,
        "first_tx_time": first_tx_time,
        "source": "tonapi",
    }


__all__ = ["fetch_wallet_onchain_signals", "fetch_evm_onchain_signals"]


# ── EVM 鏈上訊號採集（路線 D，docs/plans/2026-08-11-trust-evm-onchain-signals-design.md）──
# 與 TON 的 fetch_wallet_onchain_signals 對稱——用 Etherscan V2（服務級 key）查
# EVM 錢包的首次交易時間 + 活動度。讓 EVM 用戶的鏈上資歷也算進 trust_score。

# 分鏈快取：同一個地址在 Base 與主網的首筆交易不同（信任分數兩條都查）
_EVM_TX_CACHE_KEY = "trust:evm_first_tx:{chain_id}:{address}"
_EVM_TX_CACHE_TTL = 30 * 24 * 3600  # 30 天（錢包年齡不變）


async def _fetch_evm_first_tx(address: str, chain_id: int = 1, api_key: Optional[str] = None) -> Optional[int]:
    """用 Etherscan V2 查 EVM 錢包的第一筆交易時間（unix seconds）。

    模式 B：api_key 是使用者的 BYOK key（cron/trust 重算傳入）；
    None 時 fallback 服務級 key（ETHERSCAN_SERVICE_API_KEY）。
    翻頁找最舊一筆（sort=asc, page 1 的最後一筆 = 最早的）。
    """
    cache_key = _EVM_TX_CACHE_KEY.format(chain_id=chain_id, address=address)
    cached = get_json(cache_key)
    if cached is not None:
        return cached

    key = api_key or os.getenv("ETHERSCAN_SERVICE_API_KEY", "")
    if not key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.etherscan.io/v2/api",
                params={
                    "chainid": chain_id,
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 1,  # 只要最早一筆
                    "sort": "asc",
                    "apikey": key,
                },
            )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("status") != "1":
            return None
        txs = body.get("result") or []
        if not txs:
            return None
        ts = int(txs[0].get("timeStamp", 0))
        if ts > 0:
            set_json(cache_key, ts, _EVM_TX_CACHE_TTL)
            return ts
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[onchain_signals] EVM first tx failed %s: %s", address[:12], exc)
    return None


async def fetch_evm_onchain_signals(address: str, chain_id: int = 1, api_key: Optional[str] = None) -> dict:
    """採集 EVM 錢包的鏈上年齡與活動度訊號。

    與 fetch_wallet_onchain_signals 回傳 shape 對齊：
        {first_active, tx_count, is_active, source, first_tx_time}

    Args:
        address: EVM 地址（0x + 40hex）。
        chain_id: 鏈 ID（預設 1=Ethereum mainnet）。
        api_key: 模式 B——使用者的 BYOK key；None 時 fallback 服務 key。

    無 key / API 失敗 → {"error": ...}（graceful，不影響 TON 計分）。
    """
    addr = (address or "").strip()
    if not addr or not addr.startswith("0x") or len(addr) != 42:
        return {"error": "Invalid EVM address"}

    first_tx_time = await _fetch_evm_first_tx(addr, chain_id, api_key=api_key)
    first_active: Optional[datetime] = None
    if first_tx_time is not None:
        first_active = datetime.fromtimestamp(first_tx_time, tz=timezone.utc)

    return {
        "first_active": first_active,
        "tx_count": 1 if first_active is not None else None,
        "is_active": first_active is not None,
        "first_tx_time": first_tx_time,
        "source": f"etherscan_v2_chain{chain_id}",
    }
