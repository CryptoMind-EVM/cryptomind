"""
通知資料庫操作模組
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from .connection import get_connection


def create_notification(
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    創建新通知

    Args:
        user_id: 接收通知的用戶 ID
        notification_type: 通知類型 (friend_request, message, post_interaction, system_update, announcement)
        title: 通知標題
        body: 通知內容
        data: 額外數據

    Returns:
        創建的通知對象
    """
    notification_id = f"notif_{uuid.uuid4().hex[:12]}"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notifications (id, user_id, type, title, body, data, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE, NOW())
                RETURNING id, user_id, type, title, body, data, is_read, created_at
            """,
                (
                    notification_id,
                    user_id,
                    notification_type,
                    title,
                    body,
                    Json(data) if data else None,
                ),
            )

            row = cur.fetchone()
            conn.commit()

            if row:
                return {
                    "id": row[0],
                    "user_id": row[1],
                    "type": row[2],
                    "title": row[3],
                    "body": row[4],
                    "data": row[5],
                    "is_read": row[6],
                    "created_at": row[7].isoformat() if row[7] else None,
                }
            return None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_notifications(
    user_id: str, limit: int = 50, offset: int = 0, unread_only: bool = False
) -> List[Dict[str, Any]]:
    """
    獲取用戶的通知列表

    Args:
        user_id: 用戶 ID
        limit: 返回數量限制
        offset: 偏移量
        unread_only: 是否只返回未讀通知

    Returns:
        通知列表
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if unread_only:
                cur.execute(
                    """
                    SELECT id, user_id, type, title, body, data, is_read, created_at
                    FROM notifications
                    WHERE user_id = %s AND is_read = FALSE
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """,
                    (user_id, limit, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT id, user_id, type, title, body, data, is_read, created_at
                    FROM notifications
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """,
                    (user_id, limit, offset),
                )

            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "user_id": row[1],
                    "type": row[2],
                    "title": row[3],
                    "body": row[4],
                    "data": row[5],
                    "is_read": row[6],
                    "created_at": row[7].isoformat() if row[7] else None,
                }
                for row in rows
            ]
    finally:
        conn.close()


def get_unread_count(user_id: str) -> int:
    """
    獲取用戶未讀通知數量

    Args:
        user_id: 用戶 ID

    Returns:
        未讀通知數量
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM notifications
                WHERE user_id = %s AND is_read = FALSE
            """,
                (user_id,),
            )

            result = cur.fetchone()
            return result[0] if result else 0
    finally:
        conn.close()
