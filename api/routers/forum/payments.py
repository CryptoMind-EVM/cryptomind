"""
論壇付款（發文費、打賞）——USDC on Base。

2026-09-25 DANNY：論壇付款從 TON 改成 USDC on Base（EVM 優先）。跟 premium 同一套：
伺服器簽的訂單 token（create_multichain_order）＋每張訂單唯一的金額尾數
（resolve_stable_micro）＋鏈上驗證（verify_evm_usdc_tx／verify_evm_usdc_payment）。

- 發文費付給平台收款地址（EVM_USDC_RECEIVING_ADDRESS），驗證走預設收款人。
- 打賞直接付給作者自己的 EVM 地址（身份地址或 SIWE 綁定的錢包），平台不經手。
  平台收款地址不當打賞收款人：兩邊收款人分開，一筆轉帳就不可能同時被當成
  發文費／訂閱和打賞各領一次。
- 付款人一律要是自己帳號綁定的 EVM 錢包（fail-closed，同 premium）。
- 防重放：tx hash 先轉小寫，再寫進有 UNIQUE 的欄位（posts.payment_tx_hash／
  tips.tx_hash）。RPC 對 hex 大小寫不敏感——不轉小寫的話，同一筆付款換個大小寫
  就能再領一次。
"""

import math
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request

from api import payment_rails
from api.payment_rails import RAIL_EVM_USDC
from api.routers.premium import _bound_chain_addresses
from core import platform
from core.config import (
    FORUM_TIP_DEFAULT_USD,
    FORUM_TIP_MAX_USD,
    FORUM_TIP_MIN_USD,
    IS_TESTNET,
    STABLE_DECIMALS,
)
from core.onchain.addresses import evm_identity_address
from core.orm.wallets_repo import CHAIN_EVM

# 訂單 token 的 "p"：跟 premium 的方案名不重疊，premium 訂單不能拿來發文／打賞，反之亦然
PLAN_POST = "forum_post"
PLAN_TIP = "forum_tip"

# 錢包直付送出後就領取；一天足夠涵蓋確認數等待與重試
ORDER_TTL_SECONDS = 24 * 3600

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_EVM_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

_BIND_FIRST = (
    "USDC payment must come from a wallet bound to your account — "
    "bind your EVM wallet in Settings first"
)


def normalize_tx_hash(tx_hash: Optional[str]) -> Optional[str]:
    """0x＋64 hex → 小寫；其他（含空值）→ None。"""
    s = (tx_hash or "").strip()
    return s.lower() if _EVM_TX_RE.fullmatch(s) else None


def ensure_usdc_payments_open(request: Request) -> None:
    """平台能力表（Telegram 內不收加密貨幣、Play 版只能 Play Billing）＋ rail 有沒有設定。"""
    plat = platform.from_request(request)
    if not platform.rail_allowed(plat, RAIL_EVM_USDC):
        raise HTTPException(
            status_code=403,
            detail=f"This payment method is not available on {plat}",
        )
    if not payment_rails.rail_available(RAIL_EVM_USDC):
        raise HTTPException(
            status_code=503, detail="USDC payments are temporarily unavailable"
        )


async def bound_payers(user_id: str) -> List[str]:
    """付款人白名單：帳號綁定的 EVM 地址。沒有就 400（建單與領取都擋）。"""
    payers = await _bound_chain_addresses(user_id, CHAIN_EVM)
    if not payers:
        raise HTTPException(status_code=400, detail=_BIND_FIRST)
    return payers


async def author_evm_addresses(author_id: str) -> List[str]:
    """作者能收打賞的 EVM 地址（小寫、去重，身份地址優先）。

    只有兩種來源：EVM 登入的身份地址（evm_0x… → 0x…），以及 user_wallets 綁定的
    EVM 錢包（SIWE 證明過所有權）。空＝不能打賞。絕不退回別的地址——作者沒綁的
    地址等於把錢送給陌生人。綁定查詢失敗時只剩身份地址（查不到的不算）。
    """
    identity = evm_identity_address(author_id)
    candidates: List[str] = [identity] if identity else []
    candidates.extend(await _bound_chain_addresses(author_id, CHAIN_EVM))

    platform_addr = (payment_rails.EVM_USDC_RECEIVING_ADDRESS or "").lower()
    out: List[str] = []
    for raw in candidates:
        addr = str(raw or "").strip()
        if not _EVM_ADDRESS_RE.fullmatch(addr):
            continue
        addr = addr.lower()
        if addr == platform_addr or addr in out:
            continue
        out.append(addr)
    return out


def resolve_tip_amount(amount: Optional[float]) -> float:
    """打賞金額（USD）：沒帶用預設；有帶要在 MIN～MAX 之間、到分為止。"""
    lo, hi = float(FORUM_TIP_MIN_USD), float(FORUM_TIP_MAX_USD)
    if amount is None:
        return round(min(max(float(FORUM_TIP_DEFAULT_USD), lo), hi), 2)
    value = float(amount)
    cents = round(value * 100) if math.isfinite(value) else 0
    if (
        not math.isfinite(value)
        or abs(value * 100 - cents) > 1e-6
        or not (lo <= value <= hi)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Tip amount must be between ${lo:.2f} and ${hi:.2f} (whole cents)",
        )
    return cents / 100


def issue_order(
    user_id: str,
    plan: str,
    usd: float,
    receiving_address: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """簽一張 USDC 訂單：金額＝USD＋唯一尾數，收款人簽進 token（client 改不了）。"""
    from api.evm_verification import create_multichain_order

    micro = payment_rails.resolve_stable_micro(usd)
    quoted = micro / (10**STABLE_DECIMALS)
    token = create_multichain_order(
        user_id,
        plan,
        RAIL_EVM_USDC,
        usd,
        quoted_amount=quoted,
        ttl_seconds=ORDER_TTL_SECONDS,
        extra={"micro": micro, "recv": receiving_address, **(extra or {})},
    )
    return {
        "success": True,
        "rail": RAIL_EVM_USDC,
        "chain": "evm",
        "asset": "USDC",
        "amount": quoted,
        "decimals": STABLE_DECIMALS,
        # 錢包直付的 transfer calldata 用（含唯一尾數）
        "micro": micro,
        "receiving_address": receiving_address,
        "token_contract": payment_rails.EVM_USDC_CONTRACT,
        "network": "base_sepolia" if IS_TESTNET else "base",
        "order_token": token["order_token"],
        "expires_at": token["expires_at"],
        "fiat_amount_usd": usd,
    }


def load_order(order_token: str, user_id: str, plan: str) -> Dict[str, Any]:
    """驗簽、驗擁有者、驗效期，並確認是這個用途的 USDC 訂單。"""
    from api.evm_verification import verify_multichain_order_token

    order = verify_multichain_order_token(order_token, user_id)
    if order.get("p") != plan or order.get("rail") != RAIL_EVM_USDC:
        raise HTTPException(status_code=400, detail="Order does not match this payment")
    try:
        micro = int(order.get("micro") or 0)
    except (TypeError, ValueError):
        micro = 0
    if micro <= 0:
        raise HTTPException(status_code=400, detail="Invalid order")
    return order


async def verify_order_payment(
    order: Dict[str, Any],
    payers: List[str],
    tx_hash: Optional[str],
    *,
    receiving_address: Optional[str] = None,
) -> str:
    """鏈上驗證這張訂單的付款，回傳（小寫的）tx hash。

    有 0x tx hash 走 receipt 驗證（錢包直付），沒有就 getLogs 掃描——兩條都驗
    精確金額、付款人綁定、確認數、訂單開立後才發生。receiving_address 沒給＝平台。
    """
    kwargs: Dict[str, Any] = {
        "payer_addresses": payers,
        "issued_ts": int(order.get("iat", 0)),
    }
    if receiving_address is not None:
        kwargs["receiving_address"] = receiving_address
    micro = int(order["micro"])
    tx = normalize_tx_hash(tx_hash)
    if tx:
        payment = await payment_rails.verify_evm_usdc_tx(tx, micro, **kwargs)
    else:
        payment = await payment_rails.verify_evm_usdc_payment(micro, **kwargs)
    verified = normalize_tx_hash(payment.get("tx_hash"))
    if not verified:
        raise HTTPException(
            status_code=502, detail="Verification returned no transaction"
        )
    return verified
