"""
Audit Log Query and Analysis API (Admin Only)

Provides endpoints for administrators to query, analyze, and monitor audit logs
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_current_user, require_admin
from api.utils import run_sync
from core.audit import AuditLogger
from core.database import get_connection

router = APIRouter(prefix="/api/admin/audit", tags=["Audit Logs"])

# 使用者視角的 audit router（非 admin；可信 AI 黑客松 Audit Log 要素）
user_router = APIRouter(prefix="/api/audit", tags=["Trust & Audit"])


class AuditLogEntry(BaseModel):
    """Audit log entry model"""

    id: int
    timestamp: datetime
    user_id: Optional[str]
    username: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    endpoint: str
    method: str
    ip_address: Optional[str]
    response_code: Optional[int]
    success: bool
    error_message: Optional[str]
    duration_ms: Optional[int]


# ---------------------------------------------------------------------------
# Sync DB helpers — run on a worker thread via run_sync, never inline on the
# event loop. AGENTS.md forbids synchronous DB I/O directly in async routes.
# ---------------------------------------------------------------------------


def _get_audit_logs_sync(
    user_id, action, start_date, end_date, success, limit, offset
):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Build query with filters
        query = """
            SELECT id, timestamp, user_id, username, action, resource_type, resource_id,
                   endpoint, method, ip_address, response_code, success, error_message, duration_ms
            FROM audit_logs
            WHERE 1=1
        """
        params = []

        if user_id:
            query += " AND user_id = %s"
            params.append(user_id)
        if action:
            action = AuditLogger._normalize_action(action)
            query += " AND action = %s"
            params.append(action)
        if start_date:
            query += " AND timestamp >= %s"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= %s"
            params.append(end_date)
        if success is not None:
            query += " AND success = %s"
            params.append(success)

        query += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        logs = [dict(zip(columns, row)) for row in cursor.fetchall()]

        # Get total count
        count_query = "SELECT COUNT(*) FROM audit_logs WHERE 1=1"
        if user_id:
            count_query += " AND user_id = %s"
        if action:
            count_query += " AND action = %s"
        if start_date:
            count_query += " AND timestamp >= %s"
        if end_date:
            count_query += " AND timestamp <= %s"
        if success is not None:
            count_query += " AND success = %s"

        cursor.execute(count_query, params[:-2])  # Exclude limit and offset
        total_count = cursor.fetchone()[0]

        return {
            "logs": logs,
            "count": len(logs),
            "total": total_count,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


def _get_suspicious_activity_sync(days):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Failed login attempts
        cursor.execute(
            """
            SELECT user_id, username, ip_address, COUNT(*) as failure_count
            FROM audit_logs
            WHERE success = FALSE
            AND timestamp >= %s
            AND action IN ('login', 'pi_sync', 'dev_login')
            GROUP BY user_id, username, ip_address
            HAVING COUNT(*) >= 3
            ORDER BY failure_count DESC
        """,
            (since,),
        )

        columns = [desc[0] for desc in cursor.description]
        failed_logins = [dict(zip(columns, row)) for row in cursor.fetchall()]

        # Failed payment attempts
        cursor.execute(
            """
            SELECT user_id, username, COUNT(*) as failure_count,
                   array_agg(DISTINCT error_message) as errors
            FROM audit_logs
            WHERE success = FALSE
            AND timestamp >= %s
            AND action IN ('payment_approve', 'payment_complete')
            GROUP BY user_id, username
            HAVING COUNT(*) >= 3
            ORDER BY failure_count DESC
        """,
            (since,),
        )

        columns = [desc[0] for desc in cursor.description]
        failed_payments = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return {
            "time_range_days": days,
            "failed_logins": failed_logins,
            "failed_payments": failed_payments,
            "alert_count": len(failed_logins) + len(failed_payments),
        }
    finally:
        conn.close()


def _get_user_activity_sync(user_id, days, limit):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        since = datetime.now(timezone.utc) - timedelta(days=days)

        cursor.execute(
            """
            SELECT id, timestamp, action, resource_type, resource_id, endpoint,
                   method, response_code, success, error_message, duration_ms
            FROM audit_logs
            WHERE user_id = %s AND timestamp >= %s
            ORDER BY timestamp DESC
            LIMIT %s
        """,
            (user_id, since, limit),
        )

        columns = [desc[0] for desc in cursor.description]
        logs = [dict(zip(columns, row)) for row in cursor.fetchall()]

        # Get summary statistics
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_actions,
                SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as successful_actions,
                SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as failed_actions,
                AVG(duration_ms) as avg_duration_ms,
                COUNT(DISTINCT DATE(timestamp)) as active_days
            FROM audit_logs
            WHERE user_id = %s AND timestamp >= %s
        """,
            (user_id, since),
        )

        stats_row = cursor.fetchone()
        stats = {
            "total_actions": stats_row[0] or 0,
            "successful_actions": stats_row[1] or 0,
            "failed_actions": stats_row[2] or 0,
            "avg_duration_ms": round(stats_row[3], 2) if stats_row[3] else 0,
            "active_days": stats_row[4] or 0,
        }

        return {
            "user_id": user_id,
            "time_range_days": days,
            "logs": logs,
            "statistics": stats,
        }
    finally:
        conn.close()


def _get_audit_stats_sync(days):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Overall statistics
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_requests,
                COUNT(DISTINCT user_id) as unique_users,
                COUNT(DISTINCT ip_address) as unique_ips,
                SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as successful_requests,
                SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as failed_requests,
                AVG(duration_ms) as avg_duration_ms,
                MAX(duration_ms) as max_duration_ms
            FROM audit_logs
            WHERE timestamp >= %s
        """,
            (since,),
        )

        stats_row = cursor.fetchone()
        overall_stats = {
            "total_requests": stats_row[0] or 0,
            "unique_users": stats_row[1] or 0,
            "unique_ips": stats_row[2] or 0,
            "successful_requests": stats_row[3] or 0,
            "failed_requests": stats_row[4] or 0,
            "avg_duration_ms": round(stats_row[5], 2) if stats_row[5] else 0,
            "max_duration_ms": stats_row[6] or 0,
        }

        # Top actions
        cursor.execute(
            """
            SELECT action, COUNT(*) as count
            FROM audit_logs
            WHERE timestamp >= %s
            GROUP BY action
            ORDER BY count DESC
            LIMIT 10
        """,
            (since,),
        )

        top_actions = [{"action": row[0], "count": row[1]} for row in cursor.fetchall()]

        return {
            "time_range_days": days,
            "overall": overall_stats,
            "top_actions": top_actions,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Async route handlers — delegate DB work to run_sync (worker thread).
# ---------------------------------------------------------------------------


@router.get("/logs", dependencies=[Depends(require_admin)])
async def get_audit_logs(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    start_date: Optional[datetime] = Query(
        None, description="Start date for time range"
    ),
    end_date: Optional[datetime] = Query(None, description="End date for time range"),
    success: Optional[bool] = Query(None, description="Filter by success status"),
    limit: int = Query(100, le=1000, description="Maximum number of results"),
    offset: int = Query(0, description="Offset for pagination"),
):
    """
    Get audit logs with optional filtering

    Returns a paginated list of audit log entries matching the specified criteria.
    """
    try:
        return await run_sync(
            _get_audit_logs_sync,
            user_id,
            action,
            start_date,
            end_date,
            success,
            limit,
            offset,
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to query audit logs")


@router.get("/suspicious", dependencies=[Depends(require_admin)])
async def get_suspicious_activity(
    days: int = Query(7, le=30, description="Number of days to look back"),
):
    """
    Get suspicious activity patterns

    Identifies potential security issues such as:
    - Multiple failed login attempts
    - High volume of failed operations
    - Unusual access patterns
    """
    try:
        return await run_sync(_get_suspicious_activity_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to analyze suspicious activity")


@router.get("/user/{user_id}", dependencies=[Depends(require_admin)])
async def get_user_activity(
    user_id: str,
    days: int = Query(7, le=90, description="Number of days to retrieve"),
    limit: int = Query(100, le=1000),
):
    """
    Get complete activity history for a specific user

    Useful for investigating user behavior or troubleshooting issues.
    """
    try:
        return await run_sync(_get_user_activity_sync, user_id, days, limit)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch user activity")


@router.get("/stats", dependencies=[Depends(require_admin)])
async def get_audit_stats(
    days: int = Query(7, le=90, description="Number of days for statistics"),
):
    """
    Get overall audit log statistics

    Provides system-wide metrics for monitoring and analysis.
    """
    try:
        return await run_sync(_get_audit_stats_sync, days)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch audit statistics")


# ──────────────────────────────────────────────────────────────────────────
# 使用者視角 audit log（可信 AI 黑客松第 5 要素 Audit Log）
# 讓使用者看到「AI agent 為我做了什麼、我同意/拒絕了什麼、何時撤銷過」
# ──────────────────────────────────────────────────────────────────────────

# 信任層相關的 action（demo 時聚焦顯示這些，過濾掉 login/logout 等例行紀錄）
_TRUST_ACTIONS = {
    "consent_high_risk_action",
    "high_risk_tool_executed",
    "scam_verdict_confirmed",
    "agent_revoked",
}


@user_router.get("/me")
async def get_my_audit_log(
    current_user: dict = Depends(get_current_user),
    days: int = Query(30, le=90, description="查詊天數"),
    limit: int = Query(50, le=200, description="最多筆數"),
):
    """目前使用者的信任層稽核紀錄（同意決策、high-risk 執行、撤銷）。

    只回傳使用者本人的紀錄（user_id 從 token 取，不可指定他人）。
    預設過濾為信任層相關 action，讓 demo 聚焦；trust_only=false 可看全部。
    """
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        result = await run_sync(_get_user_activity_sync, user_id, days, limit)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch audit records")

    # 過濾：只保留信任層 action（demo 聚焦）。例行 login/logout 不顯示。
    trust_logs = [
        log
        for log in result.get("logs", [])
        if AuditLogger._normalize_action(log.get("action")) in _TRUST_ACTIONS
    ]

    return {
        "user_id": user_id,
        "time_range_days": days,
        "logs": trust_logs,
        "total": len(trust_logs),
    }
