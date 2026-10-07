"""用 Email／Google（Reown 內嵌錢包）登入的帳號（c064）：登入時記、後台看。

Reown 免費方案每月 500 個內嵌錢包用戶、超過直接停用；將來換服務，這些人的地址會變（帳號＝地址）。
provider 是前端回報的，只拿來統計；不存 Email 本身。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.database.base import DatabaseBase

logger = logging.getLogger(__name__)

# Reown Starter（免費）方案每月內嵌錢包用戶上限，超過直接停用（reown.com/pricing，2026-09-30 查）
REOWN_FREE_MAU_LIMIT = 500


def record_login(user_id: str, address: str, provider: str) -> None:
    DatabaseBase.execute(
        """
        INSERT INTO embedded_wallet_users (user_id, address, provider)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            address = EXCLUDED.address,
            provider = EXCLUDED.provider,
            last_seen_at = NOW(),
            login_count = embedded_wallet_users.login_count + 1
        """,
        (user_id, address, provider),
    )


def stats(limit: int = 50) -> Dict[str, Any]:
    """本月（跟 Reown 的月計費對照）與累計人數、各方式人數、最近登入名單。"""
    month = DatabaseBase.query_one(
        """
        SELECT
            COUNT(*) FILTER (WHERE last_seen_at >= date_trunc('month', NOW())) AS month_active,
            COUNT(*) FILTER (WHERE first_seen_at >= date_trunc('month', NOW())) AS month_new,
            COUNT(*) AS total
        FROM embedded_wallet_users
        """
    ) or {}
    by_provider = DatabaseBase.query_all(
        """
        SELECT provider,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE last_seen_at >= date_trunc('month', NOW())) AS month_active
        FROM embedded_wallet_users
        GROUP BY provider
        ORDER BY total DESC
        """
    )
    # 對照：本月用任何 EVM 錢包登入的人（audit_logs 保留 90 天，一個月內夠用）。
    # 用有索引的 timestamp（跟 created_at 同一個 INSERT 的 now()，值一樣）
    evm = DatabaseBase.query_one(
        """
        SELECT COUNT(DISTINCT user_id) AS n FROM audit_logs
        WHERE action = 'wallet_connected' AND metadata->>'chain' = 'evm'
          AND timestamp >= date_trunc('month', NOW())
        """
    ) or {}
    recent = DatabaseBase.query_all(
        """
        SELECT e.user_id, u.username, e.provider, e.first_seen_at, e.last_seen_at, e.login_count
        FROM embedded_wallet_users e
        LEFT JOIN users u ON u.user_id = e.user_id
        ORDER BY e.last_seen_at DESC
        LIMIT %s
        """,
        (int(limit),),
    )
    return {
        "month_active": int(month.get("month_active") or 0),
        "month_new": int(month.get("month_new") or 0),
        "total": int(month.get("total") or 0),
        "free_limit": REOWN_FREE_MAU_LIMIT,
        "evm_logins_this_month": int(evm.get("n") or 0),
        "by_provider": [
            {
                "provider": r["provider"],
                "total": int(r["total"]),
                "month_active": int(r["month_active"]),
            }
            for r in by_provider
        ],
        "recent": [
            {
                "user_id": r["user_id"],
                "username": r.get("username"),
                "provider": r["provider"],
                "first_seen_at": r["first_seen_at"].isoformat() if r.get("first_seen_at") else None,
                "last_seen_at": r["last_seen_at"].isoformat() if r.get("last_seen_at") else None,
                "login_count": int(r["login_count"]),
            }
            for r in recent
        ],
    }
