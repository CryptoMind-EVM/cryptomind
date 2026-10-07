"""
Admin Statistics Dashboard
Overview and trend statistics endpoints
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import require_admin
from api.funnel import funnel_stats_sync
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core.database.connection import get_connection

router = APIRouter(tags=["Admin - Stats"])

# 「錢包用戶」：EVM 登入身份、綁了錢包（user_wallets）、或舊 TON 登入身份
# （user_id 即地址；形狀粗篩同 cron_recompute_trust，排除舊 fallback 建的假 ton_wallet）。
# 2026-09-25 前只數 auth_method='ton_wallet'——登入統一 EVM 後新用戶一個都不算。
# 用法：FROM users u ... WHERE {WALLET_USER_SQL}
WALLET_USER_SQL = """(
    left(u.user_id, 4) = 'evm_'
    OR EXISTS (SELECT 1 FROM user_wallets w WHERE w.user_id = u.user_id)
    OR (u.auth_method = 'ton_wallet'
        AND (length(u.user_id) = 48 OR u.user_id ~ '^-?[0-9]+:[0-9a-fA-F]{64}$'))
)"""


# ---------------------------------------------------------------------------
# Sync DB helpers — run on a worker thread via run_sync, never inline on the
# event loop. AGENTS.md forbids synchronous DB I/O directly in async routes.
# ---------------------------------------------------------------------------


def _admin_stats_overview_sync():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            stats = {}
            c.execute("SELECT COUNT(*) FROM users")
            stats["total_users"] = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM users WHERE DATE(created_at) = CURRENT_DATE"
            )
            stats["new_users_today"] = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM users WHERE last_active_at > NOW() - INTERVAL '24 hours'"
            )
            stats["active_today"] = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM users WHERE membership_tier IN ('pro', 'premium') AND (membership_expires_at IS NULL OR membership_expires_at > NOW())"
            )
            premium_users = c.fetchone()[0]
            stats["premium_users"] = premium_users

            c.execute("SELECT COUNT(*) FROM posts WHERE is_hidden = 0")
            stats["total_posts"] = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM forum_comments WHERE is_hidden = 0 AND type = 'comment'"
            )
            stats["total_comments"] = c.fetchone()[0]

            c.execute("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM tips")
            row = c.fetchone()
            stats["total_tips_amount"] = float(row[0])
            stats["total_tips_count"] = row[1]

            c.execute(
                "SELECT COUNT(*) FROM content_reports WHERE review_status = 'pending'"
            )
            stats["pending_reports"] = c.fetchone()[0]

            # 錢包用戶數（WALLET_USER_SQL）— 與 Visitors 分頁一致
            c.execute(f"SELECT COUNT(*) FROM users u WHERE {WALLET_USER_SQL}")
            stats["total_wallet_users"] = c.fetchone()[0]

            # ── 產品使用深度（P1-1 + P1-2）──
            # chat 訊息量（平台核心使用）
            c.execute("SELECT COUNT(*) FROM conversation_history")
            stats["total_chat_messages"] = c.fetchone()[0]

            # 今日 chat 訊息
            c.execute(
                "SELECT COUNT(*) FROM conversation_history WHERE DATE(timestamp) = CURRENT_DATE"
            )
            stats["chat_messages_today"] = c.fetchone()[0]

            # skill/memory 使用（昨天修的 PR #429 + 後續修復的功能）
            c.execute("SELECT COUNT(*) FROM user_facts")
            stats["total_memories"] = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_facts"
            )
            stats["users_with_memory"] = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM user_custom_skills")
            stats["total_custom_skills"] = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_custom_skills"
            )
            stats["users_with_custom_skill"] = c.fetchone()[0]

            # ── 經營指標（P1-2 進階）──
            # 平均每人訊息數（total_chat_messages 已算過，重用 stats）
            total_users = stats.get("total_users", 0) or 1  # 避免除 0
            stats["avg_messages_per_user"] = round(
                stats.get("total_chat_messages", 0) / total_users, 1
            )

            # 付費轉換率：曾付費的不重複使用者 / 總使用者
            #（membership_payments 無 status 欄，信任為成功；失敗在 audit_logs）
            c.execute("SELECT COUNT(DISTINCT user_id) FROM membership_payments")
            paid_users = c.fetchone()[0]
            stats["paid_users"] = paid_users
            stats["conversion_rate"] = (
                round(paid_users / total_users, 4) if total_users else 0.0
            )

            # 留存率（近似）：last_active_at 只存最新時間，無每日 log。
            # D1 近似 = 註冊滿 1 天後仍活躍（last_active_at >= created_at + 24h）
            # 的使用者 / 註冊滿 1 天的使用者。
            c.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE created_at <= NOW() - INTERVAL '1 day'
                    ) AS eligible_d1,
                    COUNT(*) FILTER (
                        WHERE created_at <= NOW() - INTERVAL '1 day'
                          AND last_active_at IS NOT NULL
                          AND last_active_at >= created_at + INTERVAL '1 day'
                    ) AS retained_d1,
                    COUNT(*) FILTER (
                        WHERE created_at <= NOW() - INTERVAL '7 days'
                    ) AS eligible_d7,
                    COUNT(*) FILTER (
                        WHERE created_at <= NOW() - INTERVAL '7 days'
                          AND last_active_at IS NOT NULL
                          AND last_active_at >= created_at + INTERVAL '7 days'
                    ) AS retained_d7
                FROM users
                """
            )
            row = c.fetchone()
            eligible_d1, retained_d1, eligible_d7, retained_d7 = row
            stats["retention_d1"] = (
                round(retained_d1 / eligible_d1, 4) if eligible_d1 else 0.0
            )
            stats["retention_d7"] = (
                round(retained_d7 / eligible_d7, 4) if eligible_d7 else 0.0
            )
            stats["retention_d1_sample"] = {"retained": retained_d1, "eligible": eligible_d1}
            stats["retention_d7_sample"] = {"retained": retained_d7, "eligible": eligible_d7}

            return {"success": True, **stats}
    finally:
        conn.close()


def _admin_stats_users_sync(days):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM users
                    WHERE created_at >= NOW() - INTERVAL %s
                    GROUP BY DATE(created_at)
                    ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            rows = c.fetchall()
            data = [{"date": r[0].isoformat(), "count": r[1]} for r in rows]
            return {"success": True, "data": data, "days": days}
    finally:
        conn.close()


def _admin_stats_forum_sync(days):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM posts
                    WHERE created_at >= NOW() - INTERVAL %s
                    GROUP BY DATE(created_at)
                    ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            posts = [
                {"date": r[0].isoformat(), "count": r[1]} for r in c.fetchall()
            ]

            c.execute(
                """
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM forum_comments
                    WHERE created_at >= NOW() - INTERVAL %s AND type = 'comment'
                    GROUP BY DATE(created_at)
                    ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            comments = [
                {"date": r[0].isoformat(), "count": r[1]} for r in c.fetchall()
            ]

            return {
                "success": True,
                "posts": posts,
                "comments": comments,
                "days": days,
            }
    finally:
        conn.close()


def _admin_stats_revenue_sync(days):
    """每日收入趨勢：tips（打賞）+ membership_payments（會員訂閱）。

    回傳 {tips: [{date, amount}], memberships: [{date, amount}]} 供前端 Revenue
    雙線圖。早期只查 membership_payments 且欄位叫 total_pi，與前端讀的
    data.tips/data.memberships[].amount 完全對不上 → 圖永遠空白（P0-1 修復）。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                    SELECT DATE(created_at) AS date, COALESCE(SUM(amount), 0) AS total
                    FROM tips
                    WHERE created_at >= NOW() - INTERVAL %s
                    GROUP BY DATE(created_at)
                    ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            tips = [
                {"date": r[0].isoformat(), "amount": float(r[1])}
                for r in c.fetchall()
            ]

            c.execute(
                """
                    SELECT DATE(created_at) AS date, COALESCE(SUM(amount), 0) AS total
                    FROM membership_payments
                    WHERE created_at >= NOW() - INTERVAL %s
                    GROUP BY DATE(created_at)
                    ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            memberships = [
                {"date": r[0].isoformat(), "amount": float(r[1])}
                for r in c.fetchall()
            ]

            return {
                "success": True,
                "tips": tips,
                "memberships": memberships,
                "days": days,
            }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Async route handlers — delegate DB work to run_sync (worker thread).
# ---------------------------------------------------------------------------


def _admin_stats_feature_usage_sync(days):
    """新功能面使用計數（audit_logs 既有資料；studio/discover/public/presets）。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                    SELECT endpoint, COUNT(*) as count
                    FROM audit_logs
                    WHERE created_at >= NOW() - INTERVAL %s
                      AND (endpoint LIKE '/api/studio%%'
                           OR endpoint LIKE '/api/discover%%'
                           OR endpoint LIKE '/api/public%%'
                           OR endpoint LIKE '/api/agent-presets%%'
                           OR endpoint LIKE '/api/agent-profiles%%')
                    GROUP BY endpoint
                    ORDER BY count DESC
                    LIMIT 50
                """,
                (f"{days} days",),
            )
            endpoints = [
                {"endpoint": r[0], "count": r[1]} for r in c.fetchall()
            ]
            c.execute(
                """
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM audit_logs
                    WHERE created_at >= NOW() - INTERVAL %s
                      AND (endpoint LIKE '/api/studio%%'
                           OR endpoint LIKE '/api/discover%%'
                           OR endpoint LIKE '/api/public%%')
                    GROUP BY DATE(created_at)
                    ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            daily = [
                {"date": r[0].isoformat() if r[0] else None, "count": r[1]}
                for r in c.fetchall()
            ]
        return {"endpoints": endpoints, "daily": daily}
    finally:
        conn.close()

@router.get("/stats/feature-usage")
@limiter.limit("30/minute")
async def admin_stats_feature_usage(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    admin_user: dict = Depends(require_admin),
):
    """新功能面（Studio/Discover/Public share/Presets）使用計數"""
    try:
        return await run_sync(_admin_stats_feature_usage_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch feature usage")

@router.get("/stats/overview")
@limiter.limit("30/minute")
async def admin_stats_overview(request: Request, admin_user: dict = Depends(require_admin)):
    """概覽統計數據"""
    try:
        return await run_sync(_admin_stats_overview_sync)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch overview statistics")


@router.get("/stats/agent-runs")
@limiter.limit("30/minute")
async def admin_stats_agent_runs(
    request: Request,
    hours: int = Query(24, ge=1, le=24 * 30),
    admin_user: dict = Depends(require_admin),
):
    """Agent 運行指標（RunMetrics 滾動存放）＋每日早報統計（tier 3 觀測項）。"""
    from core.agents import run_metrics_store
    from core.daily_brief import store as brief_store

    try:
        runs = await run_sync(run_metrics_store.summary, float(hours))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 — 指標壞掉回空，不擋面板
        runs = {"hours": hours, "runs": 0, "error": "unavailable"}
    try:
        brief = await run_sync(brief_store.admin_stats)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001
        brief = {"error": "unavailable"}
    return {"success": True, "runs": runs, "brief": brief}


@router.get("/stats/users")
@limiter.limit("30/minute")
async def admin_stats_users(
    request: Request,
    days: int = Query(30, ge=7, le=90), admin_user: dict = Depends(require_admin)
):
    """用戶增長趨勢"""
    try:
        return await run_sync(_admin_stats_users_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch user statistics")


@router.get("/stats/forum")
@limiter.limit("30/minute")
async def admin_stats_forum(
    request: Request,
    days: int = Query(30, ge=7, le=90), admin_user: dict = Depends(require_admin)
):
    """論壇活動趨勢"""
    try:
        return await run_sync(_admin_stats_forum_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch forum statistics")


@router.get("/stats/revenue")
@limiter.limit("30/minute")
async def admin_stats_revenue(
    request: Request,
    days: int = Query(30, ge=7, le=90), admin_user: dict = Depends(require_admin)
):
    """收入趨勢"""
    try:
        return await run_sync(_admin_stats_revenue_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch revenue statistics")


# ---------------------------------------------------------------------------
# Visitors — chat 訪客 + 錢包連接追蹤（DANNY 需求：看誰進站/連錢包）
# 資料來源：audit_logs（action='chat_page_visited' / 'wallet_connected'），
# 由 analysis.py:GET /api/chat/sessions 與 user.py:POST /api/user/evm-login 寫入。
# ---------------------------------------------------------------------------


def _admin_stats_visitors_summary_sync():
    """訪客 KPI：今日進 chat 人數、今日新連錢包、錢包用戶總數、連錢包造訪比例。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            # 今日（UTC 日起）不重複造訪 chat 的使用者數
            c.execute(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM audit_logs
                WHERE action = 'chat_page_visited'
                  AND DATE(created_at) = CURRENT_DATE
                  AND user_id IS NOT NULL
                """
            )
            chat_visitors_today = c.fetchone()[0]

            # 今日新連錢包（evm-login 成功；舊資料含 ton-login）
            c.execute(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM audit_logs
                WHERE action = 'wallet_connected'
                  AND DATE(created_at) = CURRENT_DATE
                  AND user_id IS NOT NULL
                """
            )
            wallet_connects_today = c.fetchone()[0]

            # 錢包用戶總數（WALLET_USER_SQL）
            c.execute(f"SELECT COUNT(*) FROM users u WHERE {WALLET_USER_SQL}")
            total_wallet_users = c.fetchone()[0]

            # 近 7 日造訪 chat 的不重複使用者中，有多少是錢包用戶
            c.execute(
                f"""
                SELECT COUNT(DISTINCT al.user_id)
                FROM audit_logs al
                JOIN users u ON u.user_id = al.user_id
                WHERE al.action = 'chat_page_visited'
                  AND al.created_at >= NOW() - INTERVAL '7 days'
                  AND {WALLET_USER_SQL}
                """
            )
            wallet_visitors_7d = c.fetchone()[0]

            c.execute(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM audit_logs
                WHERE action = 'chat_page_visited'
                  AND created_at >= NOW() - INTERVAL '7 days'
                  AND user_id IS NOT NULL
                """
            )
            total_visitors_7d = c.fetchone()[0]

            return {
                "success": True,
                "chat_visitors_today": chat_visitors_today,
                "wallet_connects_today": wallet_connects_today,
                "total_wallet_users": total_wallet_users,
                "wallet_visitors_7d": wallet_visitors_7d,
                "total_visitors_7d": total_visitors_7d,
                "wallet_visitor_ratio_7d": (
                    round(wallet_visitors_7d / total_visitors_7d, 3)
                    if total_visitors_7d
                    else 0.0
                ),
            }
    finally:
        conn.close()


def _admin_stats_visitors_list_sync(days: int, limit: int):
    """最近造訪 chat 的使用者清單（每使用者取最後造訪時間），補 users 表欄位。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT user_id, MAX(created_at) AS last_visit
                FROM audit_logs
                WHERE action = 'chat_page_visited'
                  AND created_at >= NOW() - INTERVAL %s
                  AND user_id IS NOT NULL
                GROUP BY user_id
                ORDER BY last_visit DESC
                LIMIT %s
                """,
                (f"{days} days", limit),
            )
            visit_rows = c.fetchall()
            if not visit_rows:
                return {"success": True, "visitors": [], "days": days, "limit": limit}

            user_ids = [r[0] for r in visit_rows]
            last_visit_map = {r[0]: r[1] for r in visit_rows}

            # batch 查 users 補 username/auth_method/last_active_at（避免 N+1）
            c.execute(
                f"""
                SELECT u.user_id, u.username, u.auth_method, u.membership_tier,
                       u.created_at, u.last_active_at, {WALLET_USER_SQL} AS has_wallet
                FROM users u
                WHERE u.user_id = ANY(%s)
                """,
                (user_ids,),
            )
            user_map = {
                r[0]: {
                    "username": r[1],
                    "auth_method": r[2],
                    "membership_tier": r[3],
                    "user_created_at": r[4],
                    "last_active_at": r[5],
                    "has_wallet": bool(r[6]),
                }
                for r in c.fetchall()
            }

            # 同窗口內是否連過錢包（標記 🟢 用）
            c.execute(
                """
                SELECT DISTINCT user_id
                FROM audit_logs
                WHERE action = 'wallet_connected'
                  AND created_at >= NOW() - INTERVAL %s
                  AND user_id = ANY(%s)
                """,
                (f"{days} days", user_ids),
            )
            wallet_connected_set = {r[0] for r in c.fetchall()}

            visitors = []
            for uid, _lv in visit_rows:
                u = user_map.get(uid, {})
                lv = last_visit_map.get(uid)
                visitors.append(
                    {
                        "user_id": uid,
                        "username": u.get("username"),
                        "auth_method": u.get("auth_method"),
                        "has_wallet": bool(u.get("has_wallet")),
                        "membership_tier": u.get("membership_tier"),
                        "connected_wallet_in_window": uid in wallet_connected_set,
                        "last_chat_visit": lv.isoformat() if lv else None,
                        "last_active_at": (
                            u["last_active_at"].isoformat()
                            if u.get("last_active_at")
                            else None
                        ),
                        "user_created_at": (
                            u["user_created_at"].isoformat()
                            if u.get("user_created_at")
                            else None
                        ),
                    }
                )

            return {
                "success": True,
                "visitors": visitors,
                "days": days,
                "limit": limit,
            }
    finally:
        conn.close()


def _admin_stats_visitors_trend_sync(days: int):
    """每日 chat 訪客數 + 新連錢包數趨勢（不重複使用者）。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT DATE(created_at) AS date, COUNT(DISTINCT user_id) AS count
                FROM audit_logs
                WHERE action = 'chat_page_visited'
                  AND created_at >= NOW() - INTERVAL %s
                  AND user_id IS NOT NULL
                GROUP BY DATE(created_at)
                ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            chat_visits = [
                {"date": r[0].isoformat(), "count": r[1]} for r in c.fetchall()
            ]

            c.execute(
                """
                SELECT DATE(created_at) AS date, COUNT(DISTINCT user_id) AS count
                FROM audit_logs
                WHERE action = 'wallet_connected'
                  AND created_at >= NOW() - INTERVAL %s
                  AND user_id IS NOT NULL
                GROUP BY DATE(created_at)
                ORDER BY date ASC
                """,
                (f"{days} days",),
            )
            wallet_connects = [
                {"date": r[0].isoformat(), "count": r[1]} for r in c.fetchall()
            ]

            return {
                "success": True,
                "chat_visits": chat_visits,
                "wallet_connects": wallet_connects,
                "days": days,
            }
    finally:
        conn.close()


@router.get("/stats/visitors/summary")
@limiter.limit("30/minute")
async def admin_stats_visitors_summary(request: Request, admin_user: dict = Depends(require_admin)):
    """訪客 KPI 摘要（今日造訪 / 連錢包 / 比例）"""
    try:
        return await run_sync(_admin_stats_visitors_summary_sync)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to fetch visitors summary"
        )


@router.get("/stats/visitors")
@limiter.limit("30/minute")
async def admin_stats_visitors_list(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    admin_user: dict = Depends(require_admin),
):
    """最近造訪 chat 的使用者清單（含錢包連接狀態）"""
    try:
        return await run_sync(_admin_stats_visitors_list_sync, days, limit)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to fetch visitors list"
        )


@router.get("/stats/visitors/trend")
@limiter.limit("30/minute")
async def admin_stats_visitors_trend(
    request: Request,
    days: int = Query(30, ge=7, le=90), admin_user: dict = Depends(require_admin)
):
    """訪客 + 錢包連接每日趨勢"""
    try:
        return await run_sync(_admin_stats_visitors_trend_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to fetch visitors trend"
        )


@router.get("/stats/funnel")
@limiter.limit("30/minute")
async def admin_stats_funnel(
    request: Request, admin_user: dict = Depends(require_admin)
):
    """轉換漏斗（PR-5）：近 7／30 天各步驟人數與新帳號 7 日回訪（查詢見 api/funnel.py）"""
    try:
        return await run_sync(funnel_stats_sync)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 細節只進 log，不回給前端
        logger.warning("[admin] funnel stats failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch funnel stats")


# ---------------------------------------------------------------------------
# Wallet Monitor — 跨使用者聚合（不動 schema；警報觸發數需新表，留 follow-up）
# 資料來源：users.wallet_alert_settings JSONB。
# ---------------------------------------------------------------------------


def _admin_stats_wallet_monitor_sync():
    """錢包監測總覽：多少使用者啟用、監測多少錢包、鏈分佈、警報啟用率。

    注意：「警報觸發數」需要新的 wallet_alert_events 表（未實作），
    本端點只看「設定面」（誰啟用了、監測哪些）。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            # 啟用監測的使用者數（wallet_alert_settings 非空且 monitored_wallets 非空）
            c.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE wallet_alert_settings IS NOT NULL
                  AND wallet_alert_settings != 'null'::jsonb
                  AND jsonb_array_length(
                      COALESCE(wallet_alert_settings->'monitored_wallets', '[]'::jsonb)
                  ) > 0
                """
            )
            users_monitoring = c.fetchone()[0]

            # 監測中的錢包總數（加總每人的 monitored_wallets 長度）
            c.execute(
                """
                SELECT COALESCE(SUM(jsonb_array_length(
                    COALESCE(wallet_alert_settings->'monitored_wallets', '[]'::jsonb)
                )), 0)
                FROM users
                WHERE wallet_alert_settings IS NOT NULL
                  AND wallet_alert_settings != 'null'::jsonb
                """
            )
            total_monitored = c.fetchone()[0]

            # 鏈分佈（展開 monitored_wallets 的 chain 欄位）
            c.execute(
                """
                SELECT entry->>'chain' AS chain, COUNT(*) AS cnt
                FROM users,
                     LATERAL jsonb_array_elements(
                         COALESCE(wallet_alert_settings->'monitored_wallets', '[]'::jsonb)
                     ) AS entry
                GROUP BY chain
                ORDER BY cnt DESC
                """
            )
            chain_dist = [
                {"chain": r[0] or "unknown", "count": r[1]} for r in c.fetchall()
            ]

            # 警報類型啟用率（alerts.{incoming,outgoing,scam,...} = true 的使用者數）
            c.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE (wallet_alert_settings->'alerts'->>'incoming')::boolean) AS incoming,
                    COUNT(*) FILTER (WHERE (wallet_alert_settings->'alerts'->>'outgoing')::boolean) AS outgoing,
                    COUNT(*) FILTER (WHERE (wallet_alert_settings->'alerts'->>'scam')::boolean) AS scam,
                    COUNT(*) FILTER (WHERE (wallet_alert_settings->'alerts'->>'large_out')::boolean) AS large_out
                FROM users
                WHERE wallet_alert_settings IS NOT NULL
                  AND wallet_alert_settings != 'null'::jsonb
                """
            )
            row = c.fetchone()
            alert_adoption = {
                "incoming": row[0],
                "outgoing": row[1],
                "scam": row[2],
                "large_out": row[3],
            }

            return {
                "success": True,
                "users_monitoring": users_monitoring,
                "total_monitored_wallets": total_monitored,
                "chain_distribution": chain_dist,
                "alert_adoption": alert_adoption,
                "note": "alert-triggered count requires new wallet_alert_events table (follow-up)",
            }
    finally:
        conn.close()


@router.get("/stats/wallet-monitor")
@limiter.limit("10/minute")
async def admin_stats_wallet_monitor(request: Request, admin_user: dict = Depends(require_admin)):
    """錢包監測總覽（跨使用者聚合）"""
    try:
        return await run_sync(_admin_stats_wallet_monitor_sync)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to fetch wallet monitor stats"
        )


@router.get("/stats/login-methods")
@limiter.limit("30/minute")
async def admin_stats_login_methods(request: Request, admin_user: dict = Depends(require_admin)):
    """Email／Google 登入（Reown 內嵌錢包）人數與名單，對照 Reown 免費方案每月上限（c064）"""
    from core import embedded_wallet_users

    try:
        return {"success": True, **(await run_sync(embedded_wallet_users.stats))}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("admin login-methods stats failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch login method stats")


@router.get("/stats/moderation")
@limiter.limit("30/minute")
async def admin_stats_moderation(request: Request, admin_user: dict = Depends(require_admin)):
    """內容檢查服務（moderation 容器）狀態：發文頁顯示「暫時無法使用」時先看這裡"""
    from core.moderation.service import service_health

    return {"success": True, **(await service_health())}
