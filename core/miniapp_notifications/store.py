"""通知 token 儲存（表 miniapp_notification_tokens，c049）。

一個 fid 在一個宿主只會有一個有效 token；宿主換 token 就整個覆蓋。fid→user_id 的綁定來自
前端登入後回報（/api/miniapp/notifications/register），webhook 事件只有 fid。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.database.base import DatabaseBase

logger = logging.getLogger(__name__)


def upsert_token(
    fid: int,
    url: str,
    token: str,
    *,
    app_fid: Optional[int] = None,
    user_id: Optional[str] = None,
) -> None:
    """宿主給了新 token：同 (fid, url) 只留這一顆；有 user_id 就一併綁。"""
    DatabaseBase.execute(
        """
        INSERT INTO miniapp_notification_tokens
            (fid, app_fid, user_id, url, token, enabled, updated_at)
        VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
        ON CONFLICT (fid, url) DO UPDATE SET
            token = EXCLUDED.token,
            app_fid = COALESCE(EXCLUDED.app_fid, miniapp_notification_tokens.app_fid),
            user_id = COALESCE(EXCLUDED.user_id, miniapp_notification_tokens.user_id),
            enabled = TRUE,
            disabled_reason = NULL,
            updated_at = NOW()
        """,
        (int(fid), app_fid, user_id, url, token),
    )


def link_user(fid: int, user_id: str) -> int:
    """前端回報：這個 fid 是登入中的 user。"""
    return DatabaseBase.execute(
        "UPDATE miniapp_notification_tokens SET user_id = %s, updated_at = NOW() "
        "WHERE fid = %s AND (user_id IS NULL OR user_id <> %s)",
        (user_id, int(fid), user_id),
    )


def disable_by_fid(fid: int, reason: str) -> int:
    return DatabaseBase.execute(
        "UPDATE miniapp_notification_tokens SET enabled = FALSE, disabled_reason = %s, "
        "updated_at = NOW() WHERE fid = %s AND enabled",
        (reason, int(fid)),
    )


def disable_tokens(tokens: List[str], reason: str) -> int:
    if not tokens:
        return 0
    return DatabaseBase.execute(
        "UPDATE miniapp_notification_tokens SET enabled = FALSE, disabled_reason = %s, "
        "updated_at = NOW() WHERE token = ANY(%s)",
        (reason, list(tokens)),
    )


def mark_sent(tokens: List[str]) -> None:
    if tokens:
        DatabaseBase.execute(
            "UPDATE miniapp_notification_tokens SET last_sent_at = NOW() WHERE token = ANY(%s)",
            (list(tokens),),
        )


def tokens_for_user(user_id: str) -> List[Dict[str, Any]]:
    """[{url, token, fid}]，只回有效的。"""
    return DatabaseBase.query_all(
        "SELECT fid, url, token FROM miniapp_notification_tokens "
        "WHERE user_id = %s AND enabled ORDER BY updated_at DESC",
        (user_id,),
    )


def status_for_user(user_id: str) -> Dict[str, Any]:
    row = DatabaseBase.query_one(
        "SELECT COUNT(*) AS n, MAX(last_sent_at) AS last_sent_at "
        "FROM miniapp_notification_tokens WHERE user_id = %s AND enabled",
        (user_id,),
    ) or {"n": 0, "last_sent_at": None}
    return {
        "enabled": int(row.get("n") or 0) > 0,
        "tokens": int(row.get("n") or 0),
        "last_sent_at": row.get("last_sent_at"),
    }
