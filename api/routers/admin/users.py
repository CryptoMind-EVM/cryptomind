"""
Admin User Management
User listing, role, membership, and status management endpoints
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_current_user, require_admin
from api.middleware.rate_limit import limiter
from api.routers.notifications import notification_manager
from api.utils import run_sync
from core.database.connection import get_connection

from .schemas import (
    SetMembershipRequest,
    SetRoleRequest,
    SetStatusRequest,
    SettleUsdcRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin - Users"])


# ---------------------------------------------------------------------------
# Sync DB helpers — run on a worker thread via run_sync, never inline on the
# event loop. AGENTS.md forbids synchronous DB I/O directly in async routes.
# ---------------------------------------------------------------------------


def _list_users_sync(search, limit, offset, page):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            if search:
                like = f"%{search}%"
                c.execute(
                    """
                    SELECT user_id, username, auth_method, role, is_active,
                           membership_tier, membership_expires_at, created_at, last_active_at
                    FROM users
                    WHERE username ILIKE %s OR user_id ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """,
                    (like, like, limit, offset),
                )
                rows = c.fetchall()

                c.execute(
                    """
                    SELECT COUNT(*) FROM users
                    WHERE username ILIKE %s OR user_id ILIKE %s
                """,
                    (like, like),
                )
            else:
                c.execute(
                    """
                    SELECT user_id, username, auth_method, role, is_active,
                           membership_tier, membership_expires_at, created_at, last_active_at
                    FROM users
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """,
                    (limit, offset),
                )
                rows = c.fetchall()

                c.execute("SELECT COUNT(*) FROM users")

            total = c.fetchone()[0]

            return {
                "users": [
                    {
                        "user_id": r[0],
                        "username": r[1],
                        "auth_method": r[2],
                        "role": r[3] or "user",
                        "is_active": r[4] if r[4] is not None else True,
                        "membership_tier": r[5] or "free",
                        "membership_expires_at": r[6].isoformat() if r[6] else None,
                        "created_at": r[7].isoformat() if r[7] else None,
                        "last_active_at": r[8].isoformat() if r[8] else None,
                    }
                    for r in rows
                ],
                "total": total,
                "page": page,
                "limit": limit,
            }
    finally:
        conn.close()


def _get_user_detail_sync(user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT user_id, username, auth_method, role, is_active,
                       membership_tier, membership_expires_at, created_at, last_active_at
                FROM users WHERE user_id = %s
            """,
                (user_id,),
            )
            r = c.fetchone()
            if not r:
                return None

            # 產品使用深度（P1-4 user detail 擴充）— 一次連線多個輕量 COUNT。
            # 容錯：表/欄位缺失（ProgrammingError）回 0，不擋主資訊。
            # 每個 probe 失敗後 rollback 清掉 transaction-abort 狀態，
            # 讓後續 query 不被連帶拖垮（PG transaction abort cascade）。
            # 只 catch ProgrammingError（含 UndefinedTable/UndefinedColumn），
            # 連線錯誤（OperationalError 等）正確上拋（AGENTS.md 禁止 bare except）。
            import psycopg2  # noqa: PLC0415

            chat_count = memory_count = skill_count = 0
            recent_audit = []
            probes = [
                (
                    "SELECT COUNT(*) FROM conversation_history WHERE user_id = %s",
                    "chat",
                ),
                ("SELECT COUNT(*) FROM user_facts WHERE user_id = %s", "memory"),
                ("SELECT COUNT(*) FROM user_custom_skills WHERE user_id = %s", "skill"),
            ]
            for sql, kind in probes:
                try:
                    c.execute(sql, (user_id,))
                    val = c.fetchone()[0]
                    if kind == "chat":
                        chat_count = val
                    elif kind == "memory":
                        memory_count = val
                    else:
                        skill_count = val
                except psycopg2.ProgrammingError:
                    conn.rollback()  # 清 abort 狀態，讓後續 probe 不受影響
            try:
                c.execute(
                    """
                    SELECT action, success, created_at
                    FROM audit_logs
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (user_id,),
                )
                recent_audit = [
                    {
                        "action": row[0],
                        "success": row[1],
                        "at": row[2].isoformat() if row[2] else None,
                    }
                    for row in c.fetchall()
                ]
            except psycopg2.ProgrammingError:
                conn.rollback()

            # 錢包：身份本身（evm_／舊 TON）或 user_wallets 綁定——不再看 auth_method
            from core.database.user import get_user_wallet_status

            wallet = get_user_wallet_status(r[0])

            return {
                "user_id": r[0],
                "username": r[1],
                "auth_method": r[2],
                "role": r[3] or "user",
                "is_active": r[4] if r[4] is not None else True,
                "membership_tier": r[5] or "free",
                "membership_expires_at": r[6].isoformat() if r[6] else None,
                "created_at": r[7].isoformat() if r[7] else None,
                "last_active_at": r[8].isoformat() if r[8] else None,
                # 擴充欄位
                "chat_message_count": chat_count,
                "memory_count": memory_count,
                "custom_skill_count": skill_count,
                "has_wallet": wallet["has_wallet"],
                "wallet_address": wallet["wallet_address"],
                "recent_audit": recent_audit,
            }
    finally:
        conn.close()


def _set_user_role_sync(user_id, role, admin_user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT role FROM users WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            if not row:
                return None
            old_role = row[0] or "user"

            # P0-4 自我保護：禁止 admin 降級自己（防誤操作鎖死）。
            if user_id == admin_user_id and role != "admin":
                return {"error": "cannot demote yourself"}

            # P0-4 最後 admin 保護：若要把現任 admin 降級，且系統只剩這一個 admin，
            # 拒絕（避免沒人能管理）。回 None 會被 caller 當 404，所以用 error 格式。
            if old_role == "admin" and role != "admin":
                c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
                admin_count = c.fetchone()[0]
                if admin_count <= 1:
                    return {"error": "cannot demote the last admin"}

            c.execute(
                "UPDATE users SET role = %s WHERE user_id = %s",
                (role, user_id),
            )

            # Audit log（獨立保護：config_audit_log 表不存在或寫入失敗時，不該
            # 讓主操作 rollback — 否則 admin 連 role/membership/status 都改不了。
            # 主操作先 commit，audit 失敗只 log warning。）
            conn.commit()
            try:
                with conn.cursor() as ac:
                    ac.execute(
                        """
                        INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by)
                        VALUES (%s, %s, %s, %s)
                    """,
                        (
                            f"user_role:{user_id}",
                            old_role,
                            role,
                            admin_user_id,
                        ),
                    )
                conn.commit()
            except Exception as audit_exc:
                logger.warning(
                    f"[admin] role audit log failed (non-fatal): {audit_exc}"
                )
            return {"old_role": old_role, "new_role": role}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def _set_user_membership_upgrade_sync(user_id, months, admin_user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by)
                VALUES (%s, %s, %s, %s)
            """,
                (
                    f"user_membership:{user_id}",
                    "free",
                    f"pro:{months}mo",
                    admin_user_id,
                ),
            )
            conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        logger.warning(
            "Failed to write membership upgrade audit log for %s",
            user_id,
            exc_info=True,
        )
    finally:
        conn.close()


def _set_user_membership_downgrade_sync(user_id, admin_user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by)
                VALUES (%s, %s, %s, %s)
            """,
                (
                    f"user_membership:{user_id}",
                    "pro",
                    "free",
                    admin_user_id,
                ),
            )
            conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        logger.warning(
            "Failed to write membership downgrade audit log for %s",
            user_id,
            exc_info=True,
        )
    finally:
        conn.close()


def _set_user_status_sync(user_id, active, reason, admin_user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT is_active FROM users WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            if not row:
                return None

            old_status = "active" if row[0] else "suspended"
            new_status = "active" if active else "suspended"

            # P0-4 自我保護：禁止 admin 停權自己（防誤操作鎖死）。
            if user_id == admin_user_id and not active:
                return {"error": "cannot suspend your own account"}

            c.execute(
                "UPDATE users SET is_active = %s WHERE user_id = %s",
                (active, user_id),
            )

            # Audit log（容錯：config_audit_log 表可能不存在，不擋主操作）
            reason_str = f" reason:{reason}" if reason else ""
            conn.commit()
            try:
                with conn.cursor() as ac:
                    ac.execute(
                        """
                        INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by)
                        VALUES (%s, %s, %s, %s)
                    """,
                        (
                            f"user_status:{user_id}",
                            old_status,
                            f"{new_status}{reason_str}",
                            admin_user_id,
                        ),
                    )
                conn.commit()
            except Exception as audit_exc:
                logger.warning(
                    f"[admin] status audit log failed (non-fatal): {audit_exc}"
                )
            return {"old_status": old_status, "new_status": new_status}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def _bootstrap_admin_sync(user_id, current_user):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            # Check existing admins
            c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
            admin_count = c.fetchone()[0]

            if admin_count > 0:
                # Already has admin, check if current user is admin
                if current_user.get("role") != "admin":
                    raise PermissionError("Only admins can create new admins")
                # Current user is admin, allow setting new admin
                c.execute(
                    "UPDATE users SET role = 'admin' WHERE user_id = %s", (user_id,)
                )
            else:
                # No admin yet, allow self-promotion — 只能是自己
                if user_id != current_user.get("user_id"):
                    raise PermissionError("Bootstrap can only promote yourself")
                c.execute(
                    "UPDATE users SET role = 'admin' WHERE user_id = %s", (user_id,)
                )

            # Audit log
            c.execute(
                """
                INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by)
                VALUES (%s, %s, %s, %s)
            """,
                ("bootstrap_admin", None, user_id, current_user["user_id"]),
            )

            conn.commit()
            return True
    except PermissionError as e:
        conn.rollback()
        raise e
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Async route handlers — delegate DB work to run_sync (worker thread).
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(
    search: str = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin_user: dict = Depends(require_admin),
):
    """搜尋/列出用戶"""
    offset = (page - 1) * limit
    result = await run_sync(_list_users_sync, search, limit, offset, page)
    return {"success": True, **result}


@router.get("/users/{user_id}")
async def get_user_detail(user_id: str, admin_user: dict = Depends(require_admin)):
    """獲取單一用戶詳情"""
    user = await run_sync(_get_user_detail_sync, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "user": user}


@router.put("/users/{user_id}/role")
@limiter.limit("20/minute")
async def set_user_role(
    user_id: str,
    request: Request,
    req: SetRoleRequest,
    admin_user: dict = Depends(require_admin),
):
    """設定用戶角色"""
    result = await run_sync(
        _set_user_role_sync, user_id, req.role, admin_user["user_id"]
    )
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"success": True, **result}


_SETTLE_PLAN_MONTHS = {"premium_monthly": 1, "premium_yearly": 12}


@router.post("/users/{user_id}/settle-usdc")
@limiter.limit("10/minute")
async def settle_usdc_payment(
    user_id: str,
    request: Request,
    req: SettleUsdcRequest,
    admin_user: dict = Depends(require_admin),
):
    """手動核銷一筆 USDC 付款（2026-09-12 DANNY 拍板）。

    自動驗證是 fail-closed 的 payer binding＋訂單金額尾數；訂單過期、付錯錢包、
    交易所提幣這些對不上的個案，錢已經到我們的收款地址卻沒法 claim。這裡由
    admin 認定「這筆 tx 屬於這個使用者」——仍驗：鏈上成功、USDC 合約、收款是
    我們的地址、確認數足夠、金額 ≥ 方案價；tx_hash UNIQUE 擋重複核銷。
    """
    from api.payment_rails import inspect_evm_usdc_receipt
    from core.config import PREMIUM_USD_PRICES, STABLE_DECIMALS
    from core.database.user import upgrade_to_pro

    months = _SETTLE_PLAN_MONTHS[req.plan]
    receipt = await inspect_evm_usdc_receipt(req.tx_hash)
    anchor_micro = int(
        round(float(PREMIUM_USD_PRICES.get(req.plan, 0)) * 10**STABLE_DECIMALS)
    )
    if anchor_micro <= 0:
        raise HTTPException(status_code=500, detail="Pricing is not configured")
    if receipt["amount_micro"] < anchor_micro:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Transfer amount {receipt['amount_micro'] / 10**STABLE_DECIMALS:.6f} USDC "
                f"is below the {req.plan} price"
            ),
        )
    # 記實收的 USDC（鏈上金額，≥ 方案價），不是 TON 定價 × 月數
    paid_amount = receipt["amount_micro"] / 10**STABLE_DECIMALS
    try:
        ok = await run_sync(
            lambda: upgrade_to_pro(
                user_id=user_id,
                months=months,
                tx_hash=req.tx_hash,
                amount=paid_amount,
            )
        )
    except ValueError as e:
        msg = str(e)
        if "已被處理" in msg or "already" in msg.lower() or "hash" in msg.lower():
            raise HTTPException(
                status_code=409, detail="This transaction was already settled"
            )
        raise HTTPException(status_code=400, detail=msg)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info(
        "admin %s settled USDC tx=%s for user %s plan=%s micro=%s sender=%s",
        admin_user.get("user_id"),
        req.tx_hash[:18],
        user_id,
        req.plan,
        receipt["amount_micro"],
        receipt.get("sender"),
    )
    return {
        "success": True,
        "months": months,
        "amount_micro": receipt["amount_micro"],
        "sender": receipt.get("sender"),
    }


@router.put("/users/{user_id}/membership")
@limiter.limit("20/minute")
async def set_user_membership(
    user_id: str,
    request: Request,
    req: SetMembershipRequest,
    admin_user: dict = Depends(require_admin),
):
    """設定用戶會員等級"""
    if req.tier == "pro":
        import time

        from core.database.user import upgrade_to_pro

        try:
            await run_sync(
                lambda: upgrade_to_pro(
                    user_id,
                    req.months,
                    # 每次授予唯一 hash：固定 admin_grant_<admin_uid> 會撞
                    # upgrade_to_pro 的防重放檢查（同一 admin 只能授一次，
                    # 第二次起全部 400「此交易已被處理」）。
                    f"admin_grant_{admin_user['user_id']}_{user_id}_{int(time.time())}",
                )
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Audit log（容錯：config_audit_log 表可能不存在，不擋主操作）
        try:
            await run_sync(
                _set_user_membership_upgrade_sync,
                user_id,
                req.months,
                admin_user["user_id"],
            )
        except Exception as audit_exc:
            logger.warning(
                f"[admin] membership upgrade audit failed (non-fatal): {audit_exc}"
            )
        return {"success": True, "tier": "pro", "months": req.months}

    else:
        # Downgrade to free
        from core.database.user import expire_user_membership

        await run_sync(expire_user_membership, user_id)

        # Audit log（容錯）
        try:
            await run_sync(
                _set_user_membership_downgrade_sync,
                user_id,
                admin_user["user_id"],
            )
        except Exception as audit_exc:
            logger.warning(
                f"[admin] membership downgrade audit failed (non-fatal): {audit_exc}"
            )
        return {"success": True, "tier": "free"}


@router.put("/users/{user_id}/status")
@limiter.limit("20/minute")
async def set_user_status(
    user_id: str,
    request: Request,
    req: SetStatusRequest,
    admin_user: dict = Depends(require_admin),
):
    """封鎖/解封用戶"""
    result = await run_sync(
        _set_user_status_sync, user_id, req.active, req.reason, admin_user["user_id"]
    )
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    # If suspending, push force-logout via WebSocket
    if not req.active:
        try:
            await notification_manager.send_to_user(
                user_id,
                {
                    "type": "force_logout",
                    "reason": req.reason or "Account suspended by admin",
                },
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            logger.debug(
                "Failed to send force_logout notification to %s", user_id, exc_info=True
            )

    return {"success": True, **result}


# ============================================================================
# Bootstrap (one-time admin setup)
# ============================================================================


@router.post("/bootstrap-admin")
@limiter.limit("5/minute")
async def bootstrap_admin(
    request: Request,
    user_id: str = Query(..., description="要設為 admin 的 user_id"),
    current_user: dict = Depends(get_current_user),
):
    """
    設定第一個管理員（需要已登入的有效帳號）。
    如果系統中沒有任何 admin，允許任何登入用戶自我提升。
    如果已有 admin，則只有 admin 才能設定新 admin。

    安全：生產環境可設 ALLOW_ADMIN_BOOTSTRAP=false 來完全關閉此端點。
    """
    import os

    # 1. 環境開關
    if os.getenv("ALLOW_ADMIN_BOOTSTRAP", "false").lower() == "false":
        raise HTTPException(status_code=403, detail="Admin bootstrap is disabled")

    try:
        await run_sync(_bootstrap_admin_sync, user_id, current_user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"success": True, "message": f"User {user_id} is now an admin"}
