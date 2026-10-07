"""
好友功能資料庫操作
包含：用戶搜尋、好友請求、好友管理、封鎖功能
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .connection import get_connection

logger = logging.getLogger(__name__)

# ============================================================================
# 用戶搜尋 / 發現
# ============================================================================


def search_users(
    query: str, limit: int = 20, exclude_user_id: Optional[str] = None
) -> List[Dict]:
    """
    以用戶名搜尋用戶（部分匹配）
    用於尋找要加為好友的用戶
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        sql = """
            SELECT user_id, username, membership_tier, created_at
            FROM users
            WHERE username LIKE %s
        """
        params: List[Any] = [f"%{query}%"]

        if exclude_user_id:
            sql += " AND user_id != %s"
            params.append(exclude_user_id)

        sql += " ORDER BY username ASC LIMIT %s"
        params.append(limit)

        c.execute(sql, params)
        rows = c.fetchall()

        result = []
        for r in rows:
            created_at = r[3]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "member_since": created_at,
                }
            )
        return result
    finally:
        conn.close()


def get_public_user_profile(
    user_id: str, viewer_user_id: Optional[str] = None
) -> Optional[Dict]:
    """
    取得用戶的公開資料
    如果查看者是好友，會返回更多資訊
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        # 取得基本用戶資訊
        c.execute(
            """
            SELECT user_id, username, membership_tier, created_at
            FROM users
            WHERE user_id = %s
        """,
            (user_id,),
        )
        row = c.fetchone()

        if not row:
            return None

        created_at = row[3]
        if created_at:
            created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")

        profile = {
            "user_id": row[0],
            "username": row[1],
            "membership_tier": row[2] or "free",
            "member_since": created_at,
            "is_friend": False,
            "friend_status": None,
        }

        # 如果有查看者，檢查好友狀態（合併到主查詢，減少 1 次 DB round-trip）
        if viewer_user_id and viewer_user_id != user_id:
            c.execute(
                """
                SELECT status,
                       CASE WHEN user_id = %s THEN TRUE ELSE FALSE END AS is_requester
                FROM friendships
                WHERE (user_id = %s AND friend_id = %s)
                   OR (user_id = %s AND friend_id = %s)
                LIMIT 1
                """,
                (viewer_user_id, viewer_user_id, user_id, user_id, viewer_user_id),
            )
            fr = c.fetchone()
            profile["friend_status"] = fr[0] if fr else None
            profile["is_friend"] = (fr[0] == "accepted") if fr else False
            profile["is_requester"] = fr[1] if fr else False

        # 論壇統計 + 好友數量：合併為 1 次查詢（節省 3 次 DB round-trip）
        c.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM posts WHERE user_id = %s AND is_hidden = 0) AS post_count,
                (SELECT COALESCE(SUM(push_count), 0) FROM posts WHERE user_id = %s) AS total_pushes,
                (SELECT COUNT(*) FROM friendships
                 WHERE (user_id = %s OR friend_id = %s) AND status = 'accepted') AS friends_count
            """,
            (user_id, user_id, user_id, user_id),
        )
        stats = c.fetchone()
        profile["post_count"] = stats[0]
        profile["total_pushes"] = stats[1]
        profile["friends_count"] = stats[2]

        return profile
    finally:
        conn.close()


# ============================================================================
# 好友請求
# ============================================================================


def send_friend_request(from_user_id: str, to_user_id: str) -> Dict:
    """
    發送好友請求
    返回: {"success": bool, "message": str, "request_id": int}
    """
    if from_user_id == to_user_id:
        return {"success": False, "error": "cannot_add_self"}

    conn = get_connection()
    c = conn.cursor()
    try:
        # 檢查是否已存在任何關係
        c.execute(
            """
            SELECT id, status, user_id FROM friendships
            WHERE (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
        """,
            (from_user_id, to_user_id, to_user_id, from_user_id),
        )
        existing = c.fetchone()

        if existing:
            status = existing[1]
            requester_id = existing[2]

            if status == "accepted":
                return {"success": False, "error": "already_friends"}
            elif status == "pending":
                # 檢查方向 - 如果對方已經發送請求給我們，自動接受
                if requester_id == to_user_id:
                    # 對方發送請求給我們，自動接受
                    c.execute(
                        """
                        UPDATE friendships
                        SET status = 'accepted', updated_at = NOW()
                        WHERE user_id = %s AND friend_id = %s
                    """,
                        (to_user_id, from_user_id),
                    )
                    conn.commit()
                    return {
                        "success": True,
                        "message": "friend_added",
                        "auto_accepted": True,
                    }
                return {"success": False, "error": "request_pending"}
            elif status == "blocked":
                # 檢查是誰封鎖誰
                if requester_id == to_user_id:
                    return {"success": False, "error": "user_blocked_you"}
                return {"success": False, "error": "you_blocked_user"}
            elif status == "rejected":
                # 允許在拒絕後重新發送（更新現有記錄）
                c.execute(
                    """
                    UPDATE friendships
                    SET status = 'pending', updated_at = NOW()
                    WHERE user_id = %s AND friend_id = %s
                """,
                    (from_user_id, to_user_id),
                )
                conn.commit()
                return {
                    "success": True,
                    "message": "request_resent",
                    "request_id": existing[0],
                }

        # 建立新的好友請求
        c.execute(
            """
            INSERT INTO friendships (user_id, friend_id, status, created_at, updated_at)
            VALUES (%s, %s, 'pending', NOW(), NOW())
            RETURNING id
        """,
            (from_user_id, to_user_id),
        )
        request_id = c.fetchone()[0]
        conn.commit()

        return {"success": True, "message": "request_sent", "request_id": request_id}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


def accept_friend_request(user_id: str, requester_id: str) -> Dict:
    """
    接受好友請求
    user_id: 接受請求的用戶
    requester_id: 發送請求的用戶
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE friendships
            SET status = 'accepted', updated_at = NOW()
            WHERE user_id = %s AND friend_id = %s AND status = 'pending'
        """,
            (requester_id, user_id),
        )

        if c.rowcount == 0:
            return {"success": False, "error": "request_not_found"}

        conn.commit()
        return {"success": True, "message": "friend_added"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


def reject_friend_request(user_id: str, requester_id: str) -> Dict:
    """拒絕好友請求"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE friendships
            SET status = 'rejected', updated_at = NOW()
            WHERE user_id = %s AND friend_id = %s AND status = 'pending'
        """,
            (requester_id, user_id),
        )

        if c.rowcount == 0:
            return {"success": False, "error": "request_not_found"}

        conn.commit()
        return {"success": True, "message": "request_rejected"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


def cancel_friend_request(user_id: str, target_user_id: str) -> Dict:
    """取消已發送的好友請求"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            DELETE FROM friendships
            WHERE user_id = %s AND friend_id = %s AND status = 'pending'
        """,
            (user_id, target_user_id),
        )

        if c.rowcount == 0:
            return {"success": False, "error": "request_not_found"}

        conn.commit()
        return {"success": True, "message": "request_cancelled"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


def remove_friend(user_id: str, friend_id: str) -> Dict:
    """移除好友（解除好友關係）"""
    conn = get_connection()
    c = conn.cursor()
    try:
        # 刪除雙向的好友記錄
        c.execute(
            """
            DELETE FROM friendships
            WHERE ((user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s))
            AND status = 'accepted'
        """,
            (user_id, friend_id, friend_id, user_id),
        )

        if c.rowcount == 0:
            return {"success": False, "error": "not_friends"}

        conn.commit()
        return {"success": True, "message": "friend_removed"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


# ============================================================================
# 封鎖功能
# ============================================================================


def get_friends_list(user_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
    """取得已接受的好友列表"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT u.user_id, u.username, u.membership_tier,
                   f.updated_at as friends_since, u.last_active_at
            FROM friendships f
            JOIN users u ON (
                CASE
                    WHEN f.user_id = %s THEN f.friend_id = u.user_id
                    ELSE f.user_id = u.user_id
                END
            )
            WHERE (f.user_id = %s OR f.friend_id = %s)
            AND f.status = 'accepted'
            AND u.user_id != %s
            ORDER BY f.updated_at DESC
            LIMIT %s OFFSET %s
        """,
            (user_id, user_id, user_id, user_id, limit, offset),
        )

        rows = c.fetchall()
        result = []
        for r in rows:
            friends_since = r[3]
            last_active = r[4]
            if friends_since:
                friends_since = friends_since.strftime("%Y-%m-%d %H:%M:%S")
            if last_active:
                last_active = last_active.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "friends_since": friends_since,
                    "last_active_at": last_active,
                }
            )
        return result
    finally:
        conn.close()


def get_pending_requests_received(user_id: str, limit: int = 100) -> List[Dict]:
    """取得收到的待處理好友請求"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT u.user_id, u.username, u.membership_tier,
                   f.id as request_id, f.created_at
            FROM friendships f
            JOIN users u ON f.user_id = u.user_id
            WHERE f.friend_id = %s AND f.status = 'pending'
            ORDER BY f.created_at DESC
            LIMIT %s
        """,
            (user_id, limit),
        )

        rows = c.fetchall()
        result = []
        for r in rows:
            requested_at = r[4]
            if requested_at:
                requested_at = requested_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "request_id": r[3],
                    "requested_at": requested_at,
                }
            )
        return result
    finally:
        conn.close()


def get_pending_requests_sent(user_id: str, limit: int = 100) -> List[Dict]:
    """取得已發送的待處理好友請求"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT u.user_id, u.username, u.membership_tier,
                   f.id as request_id, f.created_at
            FROM friendships f
            JOIN users u ON f.friend_id = u.user_id
            WHERE f.user_id = %s AND f.status = 'pending'
            ORDER BY f.created_at DESC
            LIMIT %s
        """,
            (user_id, limit),
        )

        rows = c.fetchall()
        result = []
        for r in rows:
            sent_at = r[4]
            if sent_at:
                sent_at = sent_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "request_id": r[3],
                    "sent_at": sent_at,
                }
            )
        return result
    finally:
        conn.close()


def get_friendship_status(user_id: str, other_user_id: str) -> Optional[Dict]:
    """取得兩個用戶之間的好友狀態"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT id, user_id, friend_id, status, created_at, updated_at
            FROM friendships
            WHERE (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
        """,
            (user_id, other_user_id, other_user_id, user_id),
        )

        row = c.fetchone()
        if row:
            created_at = row[4]
            updated_at = row[5]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            if updated_at:
                updated_at = updated_at.strftime("%Y-%m-%d %H:%M:%S")
            return {
                "id": row[0],
                "requester_id": row[1],
                "target_id": row[2],
                "status": row[3],
                "created_at": created_at,
                "updated_at": updated_at,
                "is_requester": row[1] == user_id,
            }
        return None
    finally:
        conn.close()


def get_friends_count(user_id: str) -> int:
    """取得好友數量"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT COUNT(*) FROM friendships
            WHERE (user_id = %s OR friend_id = %s) AND status = 'accepted'
        """,
            (user_id, user_id),
        )
        return c.fetchone()[0]
    finally:
        conn.close()


def get_pending_count(user_id: str) -> int:
    """取得收到的待處理好友請求數量"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT COUNT(*) FROM friendships
            WHERE friend_id = %s AND status = 'pending'
        """,
            (user_id,),
        )
        return c.fetchone()[0]
    finally:
        conn.close()


def is_blocked(user_id: str, other_user_id: str) -> bool:
    """檢查是否有任一方封鎖對方"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT 1 FROM friendships
            WHERE ((user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s))
            AND status = 'blocked'
        """,
            (user_id, other_user_id, other_user_id, user_id),
        )
        return c.fetchone() is not None
    finally:
        conn.close()


def is_friend(user_id: str, other_user_id: str) -> bool:
    """檢查兩個用戶是否為好友"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT 1 FROM friendships
            WHERE ((user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s))
            AND status = 'accepted'
        """,
            (user_id, other_user_id, other_user_id, user_id),
        )
        return c.fetchone() is not None
    finally:
        conn.close()


def get_bulk_friendship_status(user_id: str, other_user_ids: list) -> dict:
    """
    ✅ 效能優化：批次查詢多個用戶的好友狀態，解決 N+1 問題
    一次 DB 查詢取代 N 次個別查詢

    Returns: {other_user_id: status_dict or None}
    """
    if not other_user_ids:
        return {}

    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT id, user_id, friend_id, status, created_at, updated_at
            FROM friendships
            WHERE (user_id = %s AND friend_id = ANY(%s))
               OR (friend_id = %s AND user_id = ANY(%s))
        """,
            (user_id, other_user_ids, user_id, other_user_ids),
        )

        rows = c.fetchall()
        result = {uid: None for uid in other_user_ids}

        for row in rows:
            uid1, uid2 = row[1], row[2]
            other = uid2 if uid1 == user_id else uid1
            created_at = row[4]
            updated_at = row[5]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            if updated_at:
                updated_at = updated_at.strftime("%Y-%m-%d %H:%M:%S")
            result[other] = {
                "id": row[0],
                "requester_id": row[1],
                "target_id": row[2],
                "status": row[3],
                "created_at": created_at,
                "updated_at": updated_at,
                "is_requester": row[1] == user_id,
            }
        return result
    finally:
        conn.close()
