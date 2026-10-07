"""``user_email_subscriptions`` 的資料存取（同步 psycopg；API 端用 run_sync 包）。

寫入一律走 ``DatabaseBase.execute`` 或 ``transaction()``——query_one／query_all 不 commit
（2026-09-12 行事曆事故）。token 只以 sha256 出現在這層，明文不落地。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.database.base import DatabaseBase, transaction

_COLUMNS = (
    "s.user_id, s.email, s.confirm_token_hash, s.confirm_sent_at, s.verified_at, "
    "s.unsubscribe_token_hash, s.unsubscribed_at, u.language"
)
_FROM = "FROM user_email_subscriptions s JOIN users u ON u.user_id = s.user_id"


def get_subscription(user_id: str) -> Optional[Dict[str, Any]]:
    return DatabaseBase.query_one(
        f"SELECT {_COLUMNS} {_FROM} WHERE s.user_id = %s", (user_id,)
    )


def get_active(user_id: str) -> Optional[Dict[str, Any]]:
    """已確認且沒退訂的那一列（早報只寄給這種）。"""
    return DatabaseBase.query_one(
        f"SELECT {_COLUMNS} {_FROM} WHERE s.user_id = %s "
        "AND s.verified_at IS NOT NULL AND s.unsubscribed_at IS NULL",
        (user_id,),
    )


def active_user_ids() -> set:
    rows = DatabaseBase.query_all(
        "SELECT user_id FROM user_email_subscriptions "
        "WHERE verified_at IS NOT NULL AND unsubscribed_at IS NULL"
    )
    return {str(r["user_id"]) for r in rows}


def count_recent_requests_for_email(
    email: str, exclude_user_id: str, since: datetime
) -> int:
    """防濫用：其他帳號最近要求寄確認信到這個地址幾次（拿我們的服務轟炸別人信箱）。"""
    row = DatabaseBase.query_one(
        "SELECT COUNT(*) AS n FROM user_email_subscriptions "
        "WHERE email = %s AND user_id <> %s AND confirm_sent_at >= %s",
        (email, exclude_user_id, since),
    )
    return int((row or {}).get("n") or 0)


def upsert_pending(
    user_id: str, email: str, confirm_token_hash: str, unsubscribe_token_hash: str
) -> None:
    """存成待確認：換地址、重寄確認信、退訂後重新訂閱都走這裡（舊確認 token 同時失效）。"""
    DatabaseBase.execute(
        """
        INSERT INTO user_email_subscriptions
            (user_id, email, confirm_token_hash, confirm_sent_at, verified_at,
             unsubscribe_token_hash, unsubscribed_at, created_at, updated_at)
        VALUES (%s, %s, %s, NOW(), NULL, %s, NULL, NOW(), NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            email = EXCLUDED.email,
            confirm_token_hash = EXCLUDED.confirm_token_hash,
            confirm_sent_at = EXCLUDED.confirm_sent_at,
            verified_at = NULL,
            unsubscribe_token_hash = EXCLUDED.unsubscribe_token_hash,
            unsubscribed_at = NULL,
            updated_at = NOW()
        """,
        (user_id, email, confirm_token_hash, unsubscribe_token_hash),
    )


def find_by_confirm_hash(confirm_hash: str) -> Optional[Dict[str, Any]]:
    return DatabaseBase.query_one(
        f"SELECT {_COLUMNS} {_FROM} WHERE s.confirm_token_hash = %s", (confirm_hash,)
    )


def mark_verified(
    user_id: str, confirm_hash: str, timezone: str, default_channels: List[str]
) -> bool:
    """確認成功（單次）＋把 email 加進早報頻道並開啟早報，同一個 transaction。

    UPDATE 條件帶 confirm_token_hash 並清掉它：同一個 token 點兩次（或同時點）只會成功一次。
    沒偏好列就建一列（其他欄位用表的預設值）。
    """
    with transaction() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE user_email_subscriptions
                SET verified_at = NOW(), confirm_token_hash = NULL,
                    unsubscribed_at = NULL, updated_at = NOW()
                WHERE user_id = %s AND confirm_token_hash = %s
                """,
                (user_id, confirm_hash),
            )
            if c.rowcount != 1:
                return False
            c.execute(
                """
                INSERT INTO user_brief_prefs
                    (user_id, enabled, timezone, channels, user_set, updated_at)
                VALUES (%s, TRUE, %s, %s, TRUE, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    enabled = TRUE,
                    user_set = TRUE,
                    channels = CASE
                        -- 系統記帳自動建的列（c063）：頻道是 DB 預設、少了 baseapp，從程式預設開始
                        WHEN NOT user_brief_prefs.user_set THEN EXCLUDED.channels
                        WHEN 'email' = ANY(user_brief_prefs.channels)
                            THEN user_brief_prefs.channels
                        ELSE array_append(user_brief_prefs.channels, 'email')
                    END,
                    updated_at = NOW()
                """,
                (user_id, timezone, list(default_channels) + ["email"]),
            )
            return True


def unsubscribe_by_hash(unsubscribe_hash: str) -> Optional[Dict[str, Any]]:
    """冪等：再點一次仍回同一列，退訂時間保留第一次。找不到回 None。"""
    with transaction() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE user_email_subscriptions s
                SET unsubscribed_at = COALESCE(unsubscribed_at, NOW()), updated_at = NOW()
                FROM users u
                WHERE s.unsubscribe_token_hash = %s AND u.user_id = s.user_id
                RETURNING s.user_id, u.language
                """,
                (unsubscribe_hash,),
            )
            row = c.fetchone()
            if row is None:
                return None
            return {d.name: v for d, v in zip(c.description, row)}


def set_unsubscribe_hash(user_id: str, unsubscribe_hash: str) -> None:
    DatabaseBase.execute(
        "UPDATE user_email_subscriptions SET unsubscribe_token_hash = %s, "
        "updated_at = NOW() WHERE user_id = %s",
        (unsubscribe_hash, user_id),
    )


def delete_for_user(user_id: str) -> bool:
    """使用者在設定頁移除 email：真的刪列（個資不留；退訂才保留紀錄）。

    同一個 transaction 把早報偏好的 'email' 頻道拿掉——不然設定頁重新整理後，
    隱藏但仍勾著的 Email 勾選框會在下次儲存時把它寫回去。
    """
    with transaction() as conn:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM user_email_subscriptions WHERE user_id = %s", (user_id,)
            )
            removed = c.rowcount > 0
            c.execute(
                "UPDATE user_brief_prefs SET channels = array_remove(channels, 'email'), "
                "updated_at = NOW() WHERE user_id = %s",
                (user_id,),
            )
            return removed
