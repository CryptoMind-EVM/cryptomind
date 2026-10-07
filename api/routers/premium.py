"""
Premium 會員相關 API
"""

import asyncio
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.payment_rails import (
    RAIL_EVM_USDC,
    rail_available,
    resolve_rail_quote,
    verify_evm_usdc_payment,
    verify_evm_usdc_tx,
)
from api.utils import logger, run_sync
from core import platform
from core.config import (
    EVM_USDC_RECEIVING_ADDRESS,
    FORUM_POST_FEE_USD,
    FORUM_TIP_DEFAULT_USD,
    FORUM_TIP_MAX_USD,
    FORUM_TIP_MIN_USD,
    IS_TESTNET,
    PREMIUM_USD_PRICES,
    STABLE_DECIMALS,
    TEST_MODE,
)
from core.database.user import get_user_membership, upgrade_to_pro
from core.entitlement import entitlement_summary, resolve_entitlement_for_user
from core.orm.repositories import user_repo
from core.orm.wallets_repo import payment_order_repo

# USDC 訂單有效期（2026-09-12 DANNY 拍板）：原本 TON_ORDER_TTL×6＝3 小時，
# 交易所延遲或隔天才回來按「我已付款」就 expired、錢到了不能 claim。穩定幣
# 無匯率風險，金額尾數＋tx_hash UNIQUE 已足以防重複——放寬到 7 天；超過
# 鏈上掃描窗（~6.7 小時）的付款靠貼 tx hash 走 receipt 驗證。
EVM_ORDER_TTL_SECONDS = 7 * 24 * 3600

router = APIRouter(prefix="/api/premium", tags=["Premium"])

PLAN_MONTHS = {
    "premium_monthly": 1,
    "premium_yearly": 12,
}


def _normalize_legacy_membership(legacy_membership: dict) -> dict:
    is_premium = bool(legacy_membership.get("is_premium"))
    return {
        "is_premium": is_premium,
        # legacy get_user_membership 只標記 is_expired、tier 字串保留原值；
        # 與 async 路徑同語義，以 is_premium 決定有效 tier。
        "membership_tier": "premium" if is_premium else "free",
        "days_remaining": 0,
        # legacy 形狀的 expires_at 一併透傳（isoformat；前端顯示到期日用）
        "expires_at": legacy_membership.get("expires_at"),
    }


def _should_fallback_to_legacy_membership(exc: Exception) -> bool:
    if isinstance(exc, ModuleNotFoundError):
        return True

    message = str(exc)
    return "async_generator" in message and "context manager" in message


def _record_used_payment(payment_id: str, user_id: str) -> None:
    from core.database.connection import get_connection

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO used_payments (payment_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (payment_id, user_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise ValueError("payment already used")
    finally:
        conn.close()


class UpgradeRequest(BaseModel):
    plan: Literal["premium_monthly", "premium_yearly"] = "premium_monthly"
    tx_hash: Optional[str] = None
    # TON Connect payment path
    order_token: Optional[str] = None  # signed order binding user+plan+amount
    comment: Optional[str] = None  # on-chain memo used to locate the transfer
    # Duration is fixed by the plan (PLAN_MONTHS). premium.js still sends it;
    # anything but the plan's own duration is a 422, not a silent 1-month charge.
    months: Optional[int] = None

    @model_validator(mode="after")
    def _months_match_plan(self):
        if self.months is not None and self.months != PLAN_MONTHS[self.plan]:
            raise ValueError(
                f"months must be {PLAN_MONTHS[self.plan]} for {self.plan} (or omitted)"
            )
        return self


class PaymentOrderRequest(BaseModel):
    plan: Literal["premium_monthly", "premium_yearly"] = "premium_monthly"
    # 2026-09-09 訂閱統一：只剩 evm_usdc。TON 兩軌（ton_native、ton_usdt）
    # 2026-09-25 移除，舊客戶端帶舊值 → 422。
    rail: Literal["evm_usdc"] = "evm_usdc"
    # 使用者在付款前按了「我同意，繼續付款」（開通後依條款 5.3 不退款）；簽進訂單 token。
    # 舊版前端不帶這欄＝False，不擋建單（快取中的舊頁面還要能付款）。
    refund_ack: bool = False


async def _bound_chain_addresses(user_id: str, chain: str) -> list:
    """Chain addresses bound to the account, as stored (payment payer binding).

    Not normalized — each verifier compares in its own chain's canonical form
    (TON: raw ``wc:hex``; EVM: lower-cased hex).

    Empty on repo failure — the EVM rail fails closed.
    """
    from core.orm.wallets_repo import user_wallet_repo

    try:
        return await user_wallet_repo.bound_addresses(user_id, chain)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "bound address lookup failed for %s (%s): %s", user_id, chain, exc
        )
        return []


def _memo() -> str:
    import os

    return "cm" + os.urandom(9).hex()


@router.post("/payment-order")
@limiter.limit("20/minute")
async def create_payment_order(
    request: Request,
    body: PaymentOrderRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a USD-anchored order on the EVM rail (USDC on Base).

    2026-09-09 DANNY：訂閱統一 USDC on Base——TON 兩軌不再接受下單；
    2026-09-25 起 ``/upgrade`` 的 TON claim 分支也移除。

    金額＝USD 錨定價 + 每張訂單唯一的微量尾數——鏈上掃描能把每筆轉帳
    對應到恰好一張訂單，交易所轉帳（無備註）也對得了帳。
    """
    plan = (body.plan or "premium_monthly").strip().lower()
    if plan not in PLAN_MONTHS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    rail = (body.rail or RAIL_EVM_USDC).strip().lower()
    if rail != RAIL_EVM_USDC:
        raise HTTPException(
            status_code=400,
            detail="Subscriptions accept USDC on Base only",
        )
    # 平台能力表（Google Play 版只能走 Play Billing；Telegram 內不收加密貨幣）——前端藏了不算，這裡才是閘門
    plat = platform.from_request(request)
    if not platform.rail_allowed(plat, rail):
        raise HTTPException(
            status_code=403,
            detail=f"This payment method is not available on {plat}",
        )
    usd_anchor = PREMIUM_USD_PRICES.get(plan)
    if not usd_anchor:
        raise HTTPException(status_code=500, detail="Pricing is not configured")

    from api.evm_verification import create_multichain_order

    quote = resolve_rail_quote(plan, rail, usd_anchor)
    memo = _memo()
    receiving = quote["receiving_address"]
    token = create_multichain_order(
        current_user["user_id"],
        plan,
        rail,
        usd_anchor,
        quoted_amount=quote["quoted_amount"],
        ttl_seconds=EVM_ORDER_TTL_SECONDS,  # stable rail: no FX drift, 7-day claim window
        extra={
            "memo": memo,
            "micro": quote["micro"],
            "recv": receiving,
            # 付款前同意「開通後依條款 5.3 不退款」（2026-09-28）：簽進 token、隨訂單落庫，
            # 每一筆付款都留得下「經消費者事先同意始提供」的證據（iat＝同意／建單時間）
            "refund_ack": bool(body.refund_ack),
        },
    )
    order_token = token["order_token"]
    quoted = quote["quoted_amount"]
    expires_at = token["expires_at"]
    info = {
        "rail": rail,
        "chain": quote["chain"],
        "asset": quote["asset"],
        "amount": quoted,
        "decimals": quote["decimals"],
        # micro（含唯一尾數）：錢包直付的 transfer calldata 用（前端組
        # a9059cbb＋pad；見 docs/plans/premium-wallet-direct-pay.md）
        "micro": quote["micro"],
        "receiving_address": receiving,
        "comment": memo,  # forward to TON wallets as text; EVM/exchange: ignore
        "token_contract": quote.get("token_contract"),
        "network": "base_sepolia" if IS_TESTNET else "base",
    }

    try:
        await payment_order_repo.create(
            user_id=current_user["user_id"],
            plan=plan,
            rail=rail,
            chain=info["chain"],
            asset=info["asset"],
            fiat_amount_usd=usd_anchor,
            quoted_amount=quoted,
            order_token=order_token,
            # 訂單 TTL 落庫（原固定 None——2026-09-10 事件排查時 8 張訂單
            # expires_at 全 NULL，無法區分活單/過期單）
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        )
    except Exception as exc:
        # 記錄失敗不擋付款（audit trail 用；replay 防護在 tx_hash UNIQUE）
        logger.warning("payment_orders record failed (non-fatal): %s", exc)

    return {
        "success": True,
        "plan": plan,
        **info,
        "order_token": order_token,
        "expires_at": expires_at,
        "fiat_amount_usd": usd_anchor,
    }


@router.get("/pricing")
async def get_pricing_plans():
    # pricing.premium＝USD 訂閱價（只收 USDC on Base）。premium 的 TON 動態金額
    # 2026-09-25 移除（不再每次打 TON/USD 報價）。
    usd_monthly = PREMIUM_USD_PRICES.get("premium_monthly", 12.0)
    usd_yearly = PREMIUM_USD_PRICES.get("premium_yearly", 108.0)

    return {
        "success": True,
        "pricing": {
            "premium": {
                "monthly": usd_monthly,
                "yearly": usd_yearly,  # USD 錨定（送 3 個月）
            }
        },
        "savings": {
            "premium_yearly_save": round(usd_monthly * 3, 2),
        },
        # 論壇價格（USD，USDC on Base 付款；forum-config.js 讀 forum.prices）。
        # 2026-09-25 取代 ton.prices。實際金額由論壇的 payment-order 端點簽進
        # 訂單（含唯一尾數），client 不可改。
        "forum": {
            "asset": "USDC",
            "network": "base_sepolia" if IS_TESTNET else "base",
            "prices": {
                "create_post": FORUM_POST_FEE_USD,
                "tip": FORUM_TIP_DEFAULT_USD,
                "tip_min": FORUM_TIP_MIN_USD,
                "tip_max": FORUM_TIP_MAX_USD,
            },
        },
        # 多 rail（multichain Part C）：2026-09-09 訂閱統一——只剩 USDC on
        # Base。金額＝USD 錨定（免匯率波動）。TON 訂閱軌已於同日決策移除。
        "rails": [
            {
                "rail": RAIL_EVM_USDC,
                "chain": "evm",
                "asset": "USDC",
                "available": rail_available(RAIL_EVM_USDC),
                "amounts": {
                    "premium_monthly": usd_monthly,
                    "premium_yearly": usd_yearly,
                },
                "receiving_address": EVM_USDC_RECEIVING_ADDRESS or None,
                "network": "base_sepolia" if IS_TESTNET else "base",
                "quote_expiry_minutes": None,
                "dynamic_pricing": False,
            },
        ],
    }


@router.post("/upgrade")
@limiter.limit("10/minute")
async def upgrade_to_premium(
    request: Request,
    body: UpgradeRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Upgrade to Premium membership.

    In production: requires a signed USDC-on-Base order (order_token) verified
    against an on-chain transfer from a wallet bound to the account.
    In TEST_MODE: tx_hash is optional and verification is skipped.
    """
    user_id = current_user["user_id"]

    plan = (body.plan or "premium_monthly").strip().lower()
    if plan not in PLAN_MONTHS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    months = PLAN_MONTHS[plan]

    tx_hash = body.tx_hash
    # 記進 membership_payments.amount 的實收金額；None＝沿用 upgrade_to_pro 舊算法
    paid_amount = None

    if not TEST_MODE:
        if body.order_token:
            # --- rail dispatch (multichain Part C) ---
            from api.evm_verification import verify_multichain_order_token

            order = verify_multichain_order_token(body.order_token, user_id)
            rail = order.get("rail")
            if order.get("p") != plan:
                raise HTTPException(status_code=400, detail="Plan does not match order")

            if rail == RAIL_EVM_USDC:
                # Payer binding is mandatory (fail-closed): getLogs filters
                # topics[1] to the user's bound addresses, so a transfer can
                # only ever credit the account that owns the paying wallet.
                evm_payers = await _bound_chain_addresses(user_id, "evm")
                if not evm_payers:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "USDC payment must come from a wallet bound to your "
                            "account — bind your EVM wallet in Settings first"
                        ),
                    )
                # 錢包直付（docs/plans/premium-wallet-direct-pay.md）：前端
                # eth_sendTransaction 取得 tx hash 後提交——以 receipt 驗證；
                # 無 tx_hash 則維持 getLogs 掃描（手動轉帳／交易所提幣）。
                if (
                    body.tx_hash
                    and body.tx_hash.startswith("0x")
                    and len(body.tx_hash) == 66
                ):
                    payment = await verify_evm_usdc_tx(
                        body.tx_hash,
                        int(order["micro"]),
                        payer_addresses=evm_payers,
                        issued_ts=int(order.get("iat", 0)),
                    )
                else:
                    payment = await verify_evm_usdc_payment(
                        int(order["micro"]),
                        payer_addresses=evm_payers,
                        issued_ts=int(order.get("iat", 0)),
                    )
                # 驗證要求鏈上金額＝訂單 micro，所以實收就是它（美元，含唯一尾數）
                paid_amount = int(order["micro"]) / 10**STABLE_DECIMALS
            else:
                # 已移除的 TON 軌（ton_native、ton_usdt，2026-09-25）與沒有 rail
                # 欄位的更舊 token
                raise HTTPException(status_code=400, detail="Unknown order rail")

            tx_hash = payment.get("tx_hash") or ""
            if not tx_hash:
                raise HTTPException(
                    status_code=502, detail="Verification returned no transaction"
                )
        else:
            raise HTTPException(
                status_code=400,
                detail="order_token is required",
            )
    else:
        if not tx_hash:
            import uuid

            tx_hash = f"test_{uuid.uuid4().hex[:16]}"

    try:
        current_membership = await user_repo.get_membership(user_id)

        if not current_membership:
            raise HTTPException(status_code=404, detail="User not found")

        # 先升級：upgrade_to_pro 內含 tx_hash 去重（查 membership_payments +
        # tx_hash UNIQUE 約束），且升級與記帳在同一 DB 交易內原子完成。
        # 過去的 _record_used_payment（寫另一張 used_payments 表）在升級前執行，
        # 一旦升級失敗會讓 payment「已標記 used 但會員未授予」，使用者無法重試。
        # 改為升級成功後再標記 used_payments（輔助記錄，失敗只 log）。
        try:
            success = await run_sync(
                lambda: upgrade_to_pro(
                    user_id=user_id,
                    months=months,
                    tx_hash=tx_hash,
                    amount=paid_amount,
                )
            )
        except ValueError as e:
            # tx_hash 已存在於 membership_payments → 重複提交
            msg = str(e)
            if "已被處理" in msg or "already" in msg.lower() or "hash" in msg.lower():
                raise HTTPException(
                    status_code=409, detail="Payment has already been used"
                )
            raise HTTPException(status_code=400, detail=msg)

        if not success:
            raise HTTPException(status_code=500, detail="Upgrade failed")

        # 升級成功後標記 used_payments（輔助索引；此時失敗無害——升級與
        # membership_payments 記帳已原子完成，replay 由 tx_hash UNIQUE 擋下）
        if not TEST_MODE and body.comment:
            try:
                await run_sync(lambda: _record_used_payment(body.comment, user_id))
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(
                    "Upgrade succeeded but used_payments mark failed (non-fatal): %s — %s",
                    body.comment,
                    e,
                )

        new_membership = await user_repo.get_membership(user_id)

        logger.info(
            "User %s upgraded to Premium, plan=%s, months=%d, tx_hash=%s",
            user_id,
            plan,
            months,
            tx_hash[:16] if tx_hash else "none",
        )

        return {
            "success": True,
            "message": f"Successfully upgraded to Premium for {months} month(s)!",
            "plan": plan,
            "months": months,
            "membership": new_membership,
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Premium upgrade failed for user %s: %s", user_id, e)
        raise HTTPException(
            status_code=500, detail="Upgrade failed, please try again later"
        )


@router.get("/status")
async def get_premium_status(
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]
        try:
            membership = await user_repo.get_membership(user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if not _should_fallback_to_legacy_membership(exc):
                raise
            logger.warning(
                "Async membership store unavailable, falling back to legacy DB layer: %s",
                exc,
            )
            membership = _normalize_legacy_membership(
                await run_sync(get_user_membership, user_id)
            )

        if not membership:
            raise HTTPException(status_code=404, detail="User not found")

        # Entitlement（design §9.4）：單一來源 resolve，與 cron / wallet_monitor 一致。
        ent = await run_sync(resolve_entitlement_for_user, user_id)

        return {
            "success": True,
            "membership": membership,
            "entitlement": entitlement_summary(ent),
        }

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Failed to get premium status for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to get status")
