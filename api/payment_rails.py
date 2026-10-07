"""
Payment rails — multichain design Part C.

USD-anchored pricing (定價 v3) generalized to multiple payment rails:

- ``evm_usdc``:   USDC on Base        — amount = USD anchor exactly

(Both TON rails were removed 2026-09-25: ``ton_usdt`` — USD₮ jetton on TON —
never verified in production; ``ton_native`` stopped taking orders
2026-09-09 and its claim branch in premium.py is gone too.)

Stable rails skip FX entirely (1 USDC ≈ $1), so each order carries a
tiny unique micro-unit suffix (uniqueness suffix) that makes on-chain
matching unambiguous even when several orders share the same plan amount,
and works for exchange withdrawals that cannot forward text memos.

Verification scans the chain directly (eth_getLogs) and
never trusts client-provided hashes. No SQL in this module: order rows
are inserted through the ORM write path (see register_payment_order).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from core.config import (
    EVM_CONFIRMATIONS,
    EVM_RPC_URL,
    EVM_USDC_CONTRACT,
    EVM_USDC_RECEIVING_ADDRESS,
    STABLE_DECIMALS,
)

logger = logging.getLogger(__name__)

# ERC-20 Transfer(address,address,uint256) topic0 — fixed keccak constant
ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# uniqueness suffix range (micro-units): +0.000001..0.004999 on the anchor
_SUFFIX_MICRO_MIN = 1
_SUFFIX_MICRO_MAX = 4999

# scan window for EVM logs: Base ~2s blocks; 30-min order TTL → ~900 blocks,
# doubled for safety and capped under common RPC getLogs range limits
EVM_LOG_SCAN_BLOCKS = int(os.getenv("EVM_LOG_SCAN_BLOCKS", "2000"))

# transfers predating order issuance never count (claim must target a live
# order, not an already-observed transfer); small slack for clock drift
ORDER_TIME_SLACK_SECONDS = 120

RAIL_EVM_USDC = "evm_usdc"

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def resolve_stable_micro(anchor_usd: float) -> int:
    """Anchor price in micro-units plus a random uniqueness suffix (CSPRNG)."""
    anchor_micro = int(round(float(anchor_usd) * (10**STABLE_DECIMALS)))
    span = _SUFFIX_MICRO_MAX - _SUFFIX_MICRO_MIN + 1
    suffix = _SUFFIX_MICRO_MIN + secrets.randbelow(span)
    return anchor_micro + suffix


def rail_available(rail: str) -> bool:
    """A rail is orderable only when its receiving side is configured.

    2026-09-09 DANNY：訂閱統一 USDC on Base（定位轉向 EVM 級產品）。TON 兩軌
    2026-09-25 移除——任何其他 rail 值一律不可下單。
    """
    if rail == RAIL_EVM_USDC:
        return bool(EVM_USDC_RECEIVING_ADDRESS) and bool(EVM_RPC_URL)
    return False


def resolve_rail_quote(plan: str, rail: str, usd_anchor: float) -> Dict[str, Any]:
    """Build the quote payload for one (plan, rail) pair.

    Returns fiat anchor, quoted crypto amount, and receiving info. For
    stable rails the quoted amount carries the uniqueness suffix so the
    on-chain scan can attribute a transfer to exactly one order.
    """
    if not rail_available(rail):
        raise HTTPException(
            status_code=503, detail=f"Payment rail {rail} is not available"
        )

    micro = resolve_stable_micro(usd_anchor)
    quoted = micro / (10**STABLE_DECIMALS)

    if rail == RAIL_EVM_USDC:
        return {
            "rail": rail,
            "chain": "evm",
            "asset": "USDC",
            "fiat_amount_usd": round(usd_anchor, 4),
            "quoted_amount": quoted,
            "micro": micro,
            "decimals": STABLE_DECIMALS,
            "receiving_address": EVM_USDC_RECEIVING_ADDRESS,
            "token_contract": EVM_USDC_CONTRACT,
        }
    raise HTTPException(status_code=400, detail="Unknown rail")


# ---------------------------------------------------------------------------
# on-chain verification
# ---------------------------------------------------------------------------


def _topic_for_address(address: str) -> str:
    """Left-pad a 0x address to 32 bytes for an ERC-20 indexed topic."""
    return "0x" + "0" * 24 + address.lower().replace("0x", "")


def _resolve_receiver(receiving_address: Optional[str]) -> str:
    """收款地址：None＝平台地址（premium／發文費，行為與加參數前一樣）；
    明確傳入的（論壇打賞＝作者地址）必須是合法 EVM 地址——空字串或格式錯
    直接 400，絕不退回平台地址（fail-closed：不然打賞會驗成付給平台的錢）。"""
    if receiving_address is None:
        if not EVM_USDC_RECEIVING_ADDRESS:
            raise HTTPException(
                status_code=503, detail="evm_usdc rail is not available"
            )
        return EVM_USDC_RECEIVING_ADDRESS
    if not _EVM_ADDRESS_RE.fullmatch(str(receiving_address)):
        raise HTTPException(status_code=400, detail="Invalid receiving address")
    return str(receiving_address).lower()


async def _block_timestamp(block_hex: str) -> int:
    """Fetch one block's unix timestamp (best effort; 0 on any malformed reply)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                EVM_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "eth_getBlockByNumber",
                    "params": [block_hex, False],
                },
            )
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("eth_getBlockByNumber failed for %s: %s", block_hex, exc)
        return 0
    result = body.get("result") or {}
    try:
        return int(result.get("timestamp", "0x0"), 16)
    except (TypeError, ValueError):
        return 0


async def verify_evm_usdc_payment(
    expected_micro: int,
    *,
    payer_addresses: Optional[List[str]] = None,
    issued_ts: Optional[int] = None,
    receiving_address: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan USDC Transfer logs to our receiving address (eth_getLogs).

    ``receiving_address``: None = the platform address (premium, forum post
    fee). Forum tips pass the post author's own EVM address (2026-09-25).

    Attribution rule (defense in depth, 2026-08-31 hardening):

    1. payer binding — ``topics[1]`` restricted to the user's bound EVM
       addresses (eth_getLogs OR-list). Without it, whoever verified first
       could claim someone else's transfer (attribution race). The caller
       must supply at least one bound address; unbound users are asked to
       bind first (EVM addresses are self-serve bindable).
    2. amount == expected_micro — exact per-order unique suffix match
    3. transfer must not predate order issuance (block timestamp, minus slack)
    4. EVM_CONFIRMATIONS blocks before acceptance (reorg protection, kept)
    """
    receiver = _resolve_receiver(receiving_address)
    payers = [a.lower() for a in (payer_addresses or []) if a]
    if not payers:
        raise HTTPException(
            status_code=400,
            detail=(
                "USDC payment must come from a wallet bound to your account — "
                "bind your EVM wallet in Settings first"
            ),
        )
    min_ts = (int(issued_ts) - ORDER_TIME_SLACK_SECONDS) if issued_ts else 0
    from_topics = [_topic_for_address(a) for a in payers]

    # 掃描窗下限：固定 2000 blocks（~67 分）本來是為 30 分鐘訂單 TTL 設的；
    # stable rail 的 TTL 是 3 小時（premium.py ttl*6）——付款後 >67 分才回來
    # 點驗證的話（交易所提幣正是慢路徑），log 已滑出掃描窗，永遠對不上帳
    # （2026-09-10 code review：錢已付、會員永不入帳的死路）。改依訂單
    # iat 延展至涵蓋整個 TTL，仍設上限避免 getLogs 範圍被 RPC 拒絕。
    scan_blocks = EVM_LOG_SCAN_BLOCKS
    if issued_ts:
        seconds_since_iat = max(0, int(time.time()) - int(issued_ts))
        blocks_since_iat = seconds_since_iat // 2 + 120  # Base ~2s/block＋緩衝
        scan_blocks = min(max(scan_blocks, blocks_since_iat), 12000)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            head = await client.post(
                EVM_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_blockNumber",
                    "params": [],
                },
            )
            head.raise_for_status()
            try:
                head_body = head.json()
            except ValueError:
                logger.error("eth_blockNumber returned non-JSON body")
                raise HTTPException(
                    status_code=502, detail="EVM network verification error"
                )
            latest = int(head_body.get("result", "0x0"), 16)
            from_block = max(0, latest - scan_blocks)
            logs = await _get_usdc_transfer_logs(
                client,
                from_block=from_block,
                latest=latest,
                from_topics=from_topics,
                receiver=receiver,
            )
    except httpx.HTTPError as exc:
        logger.error("EVM RPC request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to reach EVM network")

    if not isinstance(logs, list):
        logger.error("eth_getLogs returned non-list result")
        raise HTTPException(status_code=502, detail="EVM network verification error")

    rejected_time = False
    for log in logs:
        try:
            value_micro = int(log.get("data", "0x0"), 16)
            block_num = int(log.get("blockNumber", "0x0"), 16)
        except (TypeError, ValueError):
            continue
        if value_micro != expected_micro:
            continue  # exact per-order suffix — anything else belongs to another order
        confirmations = latest - block_num
        if confirmations < EVM_CONFIRMATIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Payment seen but awaiting confirmations "
                    f"({confirmations}/{EVM_CONFIRMATIONS})"
                ),
            )
        if min_ts:
            ts = await _block_timestamp(hex(block_num))
            if ts and ts < min_ts:
                rejected_time = True
                continue  # transfer predates this order — belongs to another one
        tx_hash = log.get("transactionHash") or ""
        logger.info(
            "USDC payment matched: micro=%s tx=%s", value_micro, str(tx_hash)[:18]
        )
        return {
            "tx_hash": str(tx_hash),
            "amount_micro": value_micro,
            "block": block_num,
            "confirmations": confirmations,
        }

    if rejected_time:
        raise HTTPException(
            status_code=400,
            detail="Transfer predates this order — please create a new order",
        )

    # 未綁錢包診斷（2026-09-10 code review）：綁定掃描沒找到時，不帶 payer
    # 過濾再掃一次——若「金額完全相符的轉帳」存在但來自未綁定地址，明確
    # 告知付錯錢包（可到 Settings 綁定後重驗），而不是讓使用者對著
    # "No matching payment found yet" 空轉到逾時（錢已扣、會員不入帳）。
    # min_ts 一併套用（第三輪 review）：尾數碰撞的他單舊轉帳不得誤導。
    unbound_seen = await _scan_unbound_exact_transfer(
        from_block=from_block,
        expected_micro=expected_micro,
        latest=latest,
        min_ts=min_ts,
        receiver=receiver,
    )
    if unbound_seen:
        raise HTTPException(
            status_code=400,
            detail=(
                "Payment seen but not from your bound wallet — pay from a "
                "wallet bound to your account (Settings → Wallets), or bind "
                "that wallet first, then verify again"
            ),
        )
    raise HTTPException(
        status_code=400,
        detail="No matching payment found yet — please wait for confirmation",
    )


async def inspect_evm_usdc_receipt(tx_hash: str) -> Dict[str, Any]:
    """Admin 手動核銷用：讀 receipt，回傳「打到收款地址的 USDC Transfer」。

    不綁付款人、不綁訂單金額——對應關係由 admin 自己認定（訂單過期、付錯
    錢包、交易所提幣這類 payer binding 對不上的個案）。仍驗：鏈上成功、
    合約是 USDC、收款是我們的地址、確認數足夠。
    """
    if not EVM_USDC_RECEIVING_ADDRESS:
        raise HTTPException(status_code=503, detail="evm_usdc rail is not available")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                EVM_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "eth_getTransactionReceipt",
                    "params": [tx_hash],
                },
            )
            resp.raise_for_status()
            head = await client.post(
                EVM_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "eth_blockNumber",
                    "params": [],
                },
            )
            head.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("EVM RPC request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to reach EVM network")
    try:
        receipt = resp.json().get("result")
        latest = int(head.json().get("result", "0x0"), 16)
    except ValueError:
        receipt = None
        latest = 0
    if not receipt or receipt.get("status") != "0x1":
        raise HTTPException(
            status_code=400,
            detail="Transaction not confirmed on Base yet — please wait",
        )
    recv_topic = _topic_for_address(EVM_USDC_RECEIVING_ADDRESS)
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if (
            len(topics) >= 3
            and topics[0] == ERC20_TRANSFER_TOPIC
            and log.get("address", "").lower() == EVM_USDC_CONTRACT.lower()
            and topics[2] == recv_topic
        ):
            try:
                value_micro = int(log.get("data", "0x0"), 16)
                block_num = int(log.get("blockNumber", "0x0"), 16)
            except (TypeError, ValueError):
                continue
            confirmations = latest - block_num
            if confirmations < EVM_CONFIRMATIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Payment seen but awaiting confirmations "
                        f"({confirmations}/{EVM_CONFIRMATIONS})"
                    ),
                )
            return {
                "tx_hash": str(tx_hash),
                "sender": "0x" + topics[1][-40:],
                "amount_micro": value_micro,
                "block": block_num,
                "confirmations": confirmations,
            }
    raise HTTPException(
        status_code=400,
        detail="No USDC transfer to the receiving address found in this transaction",
    )


async def verify_evm_usdc_tx(
    tx_hash: str,
    expected_micro: int,
    *,
    payer_addresses: Optional[List[str]] = None,
    issued_ts: Optional[int] = None,
    receiving_address: Optional[str] = None,
) -> Dict[str, Any]:
    """Wallet-direct-pay path: verify a submitted tx by receipt.

    Same attribution rules as the getLogs scan (fail-closed payer binding,
    exact per-order micro, post-iat block time, confirmation depth) —
    differs only in that the transfer is located by tx hash instead of
    scanned for. Design: docs/plans/premium-wallet-direct-pay.md.

    ``receiving_address``: None = the platform address; forum tips pass the
    post author's EVM address.
    """
    receiver = _resolve_receiver(receiving_address)
    payers = {a.lower() for a in (payer_addresses or []) if a}
    if not payers:
        raise HTTPException(
            status_code=400,
            detail=(
                "USDC payment must come from a wallet bound to your account — "
                "bind your EVM wallet in Settings first"
            ),
        )
    min_ts = (int(issued_ts) - ORDER_TIME_SLACK_SECONDS) if issued_ts else 0

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                EVM_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "eth_getTransactionReceipt",
                    "params": [tx_hash],
                },
            )
            resp.raise_for_status()
            head = await client.post(
                EVM_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "eth_blockNumber",
                    "params": [],
                },
            )
            head.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("EVM RPC request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to reach EVM network")

    try:
        receipt = resp.json().get("result")
        latest = int(head.json().get("result", "0x0"), 16)
    except ValueError:
        receipt = None
        latest = 0
    if not receipt or receipt.get("status") != "0x1":
        # 未上鏈（null）或執行失敗（0x0）——可重試的暫態訊息
        raise HTTPException(
            status_code=400,
            detail="Transaction not confirmed on Base yet — please wait",
        )

    recv_topic = _topic_for_address(receiver)
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if (
            len(topics) >= 3
            and topics[0] == ERC20_TRANSFER_TOPIC
            and log.get("address", "").lower() == EVM_USDC_CONTRACT.lower()
            and topics[2] == recv_topic
        ):
            try:
                value_micro = int(log.get("data", "0x0"), 16)
                block_num = int(log.get("blockNumber", "0x0"), 16)
            except (TypeError, ValueError):
                continue
            sender = "0x" + topics[1][-40:]
            if sender not in payers:
                continue  # receipt 內其他轉帳（如 refund）不關心
            if value_micro != expected_micro:
                raise HTTPException(
                    status_code=400,
                    detail="Payment amount mismatch — please create a new order",
                )
            confirmations = latest - block_num
            if confirmations < EVM_CONFIRMATIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Payment seen but awaiting confirmations "
                        f"({confirmations}/{EVM_CONFIRMATIONS})"
                    ),
                )
            if min_ts:
                ts = await _block_timestamp(hex(block_num))
                if ts and ts < min_ts:
                    raise HTTPException(
                        status_code=400,
                        detail="Transfer predates this order — please create a new order",
                    )
            logger.info(
                "USDC wallet-pay verified: tx=%s micro=%s",
                str(tx_hash)[:18],
                value_micro,
            )
            return {
                "tx_hash": str(tx_hash),
                "amount_micro": value_micro,
                "block": block_num,
                "confirmations": confirmations,
            }

    # 有 receipt 但找不到「綁定錢包→收款地址」的 USDC Transfer：
    # 付錯合約/付錯錢包/金額不符（非預期金額已被上面 mismatch 擋）——
    # 這裡涵蓋 sender 不符（付自未綁定錢包）
    raise HTTPException(
        status_code=400,
        detail=(
            "Payment seen but not from your bound wallet — pay from a wallet "
            "bound to your account (Settings → Wallets), or bind that wallet "
            "first, then verify again"
        ),
    )


async def _get_usdc_transfer_logs(
    client: httpx.AsyncClient,
    *,
    from_block: int,
    latest: int,
    from_topics,
    receiver: str,
) -> list:
    """eth_getLogs for USDC transfers to ``receiver``, aggregated in chunks.

    Base 公共 RPC 拒絕大範圍 getLogs（413 Payload Too Large——2026-09-10
    生產實測：掃描窗依訂單年齡延展後，>2000 blocks 的請求全滅，驗證
    端點 502「Unable to reach EVM network」）。以 EVM_LOG_SCAN_BLOCKS
    （實測可用的範圍）為上限分塊拉取、本地聚合。
    """
    logs: list = []
    chunk = max(1, EVM_LOG_SCAN_BLOCKS)
    start = from_block
    while start <= latest:
        end = min(start + chunk - 1, latest)
        resp = await client.post(
            EVM_RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "eth_getLogs",
                "params": [
                    {
                        "fromBlock": hex(start),
                        "toBlock": hex(end),
                        "address": EVM_USDC_CONTRACT,
                        "topics": [
                            ERC20_TRANSFER_TOPIC,
                            from_topics,
                            _topic_for_address(receiver),
                        ],
                    }
                ],
            },
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            logger.error("eth_getLogs returned non-JSON body (chunk %s-%s)", start, end)
            raise HTTPException(
                status_code=502, detail="EVM network verification error"
            )
        if body.get("error"):
            logger.error("eth_getLogs error: %s", body["error"])
            raise HTTPException(
                status_code=502, detail="EVM network verification error"
            )
        logs.extend(body.get("result") or [])
        start = end + 1
    return logs


async def _scan_unbound_exact_transfer(
    *,
    from_block: int,
    expected_micro: int,
    latest: int,
    min_ts: int = 0,
    receiver: str,
) -> bool:
    """Second-pass scan without the payer topic: any exact-amount transfer
    to ``receiver`` whose payer is NOT in the bound set?

    Diagnostic only — attribution itself never loosens (fail-closed payer
    binding stays the rule; this just turns a silent dead-end into an
    actionable error). RPC failures here degrade to "no diagnosis".
    Transfers predating the order (``min_ts``) are skipped — a foreign
    order with a colliding micro suffix must not mislead the diagnosis.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            logs = await _get_usdc_transfer_logs(
                client,
                from_block=from_block,
                latest=latest,
                from_topics=None,  # 不帶 payer 過濾（診斷用）
                receiver=receiver,
            )
    except HTTPException as exc:
        # 診斷掃描永遠 best-effort：節點不支援 topics 萬用字元（實測
        # mainnet.base.org 對 null topics 回 Invalid params）等任何失敗
        # 都降級為「無診斷」，不得讓主驗證跟著 502。
        logger.warning("unbound-payer diagnostic scan degraded: %s", exc.detail)
        return False
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("unbound-payer diagnostic scan failed (degraded): %s", exc)
        return False
    for log in logs:
        try:
            if int(log.get("data", "0x0"), 16) != expected_micro:
                continue
            block_num = int(log.get("blockNumber", "0x0"), 16)
            if latest - block_num < EVM_CONFIRMATIONS:
                continue  # unconfirmed — may still be the bound wallet's tx
        except (TypeError, ValueError):
            continue
        if min_ts:
            ts = await _block_timestamp(hex(block_num))
            if ts and ts < min_ts:
                continue  # predates this order — belongs to another one
        return True
    return False
