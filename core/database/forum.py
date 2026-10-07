"""
論壇功能相關資料庫操作
包含：看板、文章、回覆、打賞、標籤

Refactored to use DatabaseBase for unified CRUD operations.
"""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from psycopg2 import sql

from core.audit import AuditLogger

from .base import DatabaseBase
from .connection import get_connection
from .system_config import get_limits
from .user import get_user_membership

logger = logging.getLogger(__name__)


# ============================================================================
# 看板 (Boards)
# ============================================================================


def get_boards(active_only: bool = True) -> List[Dict]:
    """獲取看板列表"""
    if active_only:
        results = DatabaseBase.query_all(
            "SELECT id, name, slug, description, post_count, is_active FROM boards WHERE is_active = 1"
        )
    else:
        results = DatabaseBase.query_all(
            "SELECT id, name, slug, description, post_count, is_active FROM boards"
        )
    # Convert is_active to bool for consistency
    return [{**r, "is_active": bool(r["is_active"])} for r in results]


def get_board_by_slug(slug: str) -> Optional[Dict]:
    """根據 slug 獲取看板詳情"""
    result = DatabaseBase.query_one(
        "SELECT id, name, slug, description, post_count, is_active FROM boards WHERE slug = %s",
        (slug,),
    )
    if result:
        result["is_active"] = bool(result["is_active"])
    return result


# ============================================================================
# 文章 (Posts)
# ============================================================================


def check_daily_post_limit(user_id: str) -> Dict:
    """
    檢查用戶是否達到每日發文上限
    返回: {"allowed": bool, "count": int, "limit": int|None, "remaining": int|None}
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        membership = get_user_membership(user_id)
        is_premium = membership.get("is_premium", False)

        # 獲取對應的限制（從數據庫動態讀取）
        limits = get_limits()
        limit = (
            limits["daily_post_premium"] if is_premium else limits["daily_post_free"]
        )

        # 如果無限制，直接返回允許
        if limit is None:
            return {"allowed": True, "count": 0, "limit": None, "remaining": None}

        # 查詢今日發文數
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        c.execute(
            """
            SELECT post_count FROM user_daily_posts
            WHERE user_id = %s AND date = %s
        """,
            (user_id, today),
        )
        row = c.fetchone()
        current_count = row[0] if row else 0

        return {
            "allowed": current_count < limit,
            "count": current_count,
            "limit": limit,
            "remaining": max(0, limit - current_count),
        }
    finally:
        conn.close()


def create_post(
    board_id: int,
    user_id: str,
    category: str,
    title: str,
    content: str,
    tags: Optional[List[str]] = None,
    payment_tx_hash: Optional[str] = None,
    skip_limit_check: bool = False,
) -> Dict:
    """
    創建新文章
    返回: {"success": bool, "post_id": int} 或 {"success": False, "error": str, ...}

    skip_limit_check: 跳過限制檢查（用於 Premium 會員等特殊情況）
    """
    # 內容長度限制
    MAX_TITLE_LENGTH = 200
    MAX_CONTENT_LENGTH = 10000
    # 標籤數量上限
    MAX_TAGS_PER_POST = 5

    conn = get_connection()
    c = conn.cursor()
    try:
        # Sanitize UGC(後端縱深防護,防 stored XSS — 見 core/validators/forum_sanitizer.py)
        # 在長度檢查前 sanitize:避免攻擊者用大量被 strip 的 tag 灌長度
        from core.validators.forum_sanitizer import (
            sanitize_category,
            sanitize_post_content,
            sanitize_post_title,
        )

        title = sanitize_post_title(title)
        content = sanitize_post_content(content)
        category = sanitize_category(category)
        if tags:
            tags = [sanitize_category(t) for t in tags]

        # 檢查標題長度
        if not title or len(title.strip()) == 0:
            return {"success": False, "error": "title_required"}
        if len(title) > MAX_TITLE_LENGTH:
            return {
                "success": False,
                "error": "title_too_long",
                "max_length": MAX_TITLE_LENGTH,
                "current_length": len(title),
            }

        # 檢查內容長度
        if not content or len(content.strip()) == 0:
            return {"success": False, "error": "content_required"}
        if len(content) > MAX_CONTENT_LENGTH:
            return {
                "success": False,
                "error": "content_too_long",
                "max_length": MAX_CONTENT_LENGTH,
                "current_length": len(content),
            }

        # 檢查標籤數量
        if tags and len(tags) > MAX_TAGS_PER_POST:
            return {
                "success": False,
                "error": "too_many_tags",
                "max_tags": MAX_TAGS_PER_POST,
                "provided": len(tags),
            }

        # 檢查每日發文限制（免費會員）
        if not skip_limit_check:
            limit_check = check_daily_post_limit(user_id)
            if not limit_check["allowed"]:
                return {
                    "success": False,
                    "error": "daily_post_limit_reached",
                    "limit": limit_check["limit"],
                    "count": limit_check["count"],
                }

        # ===== 以下所有操作在單一事務中 =====

        # 1. 創建文章
        tags_json = json.dumps(tags, ensure_ascii=False) if tags else None
        c.execute(
            """
            INSERT INTO posts (board_id, user_id, category, title, content, tags, payment_tx_hash, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
        """,
            (board_id, user_id, category, title, content, tags_json, payment_tx_hash),
        )

        post_id = c.fetchone()[0]

        # 2. 更新看板文章數
        c.execute(
            "UPDATE boards SET post_count = post_count + 1 WHERE id = %s", (board_id,)
        )

        # 3. 處理標籤
        if tags:
            for tag_name in tags:
                tag_name = tag_name.strip().upper()
                if not tag_name:
                    continue
                # 創建或更新標籤
                c.execute(
                    """
                    INSERT INTO tags (name, post_count, last_used_at, created_at)
                    VALUES (%s, 1, NOW(), NOW())
                    ON CONFLICT(name) DO UPDATE SET
                        post_count = tags.post_count + 1,
                        last_used_at = NOW()
                """,
                    (tag_name,),
                )
                # 獲取標籤 ID
                c.execute("SELECT id FROM tags WHERE name = %s", (tag_name,))
                tag_id = c.fetchone()[0]
                # 建立關聯
                c.execute(
                    "INSERT INTO post_tags (post_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (post_id, tag_id),
                )

        # 4. 更新每日發文計數（關鍵：必須在同一事務中）
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        c.execute(
            """
            INSERT INTO user_daily_posts (user_id, date, post_count)
            VALUES (%s, %s, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET post_count = user_daily_posts.post_count + 1
        """,
            (user_id, today),
        )

        # 全部成功才提交
        conn.commit()
        return {"success": True, "post_id": post_id}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Create post error: {e}")
        conn.rollback()  # 全部回滾，確保數據一致性
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


def get_posts(
    board_id: Optional[int] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    include_hidden: bool = False,
) -> List[Dict]:
    """獲取文章列表"""
    conn = get_connection()
    c = conn.cursor()
    try:
        query = """
            SELECT p.id, p.board_id, p.user_id, p.category, p.title,
                   p.push_count, p.boo_count, p.comment_count, p.tips_total, p.view_count,
                   p.is_pinned, p.is_hidden, p.created_at,
                   u.username, b.name as board_name, b.slug as board_slug
            FROM posts p
            LEFT JOIN users u ON p.user_id = u.user_id
            LEFT JOIN boards b ON p.board_id = b.id
            WHERE 1=1
        """
        params: List[Any] = []

        if board_id:
            query += " AND p.board_id = %s"
            params.append(board_id)

        if category:
            query += " AND p.category = %s"
            params.append(category)

        if not include_hidden:
            query += " AND p.is_hidden = 0"

        if tag:
            query += """
                AND p.id IN (
                    SELECT pt.post_id FROM post_tags pt
                    JOIN tags t ON pt.tag_id = t.id
                    WHERE t.name = %s
                )
            """
            params.append(tag.upper())

        query += " ORDER BY p.is_pinned DESC, p.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        c.execute(query, params)
        rows = c.fetchall()

        result = []
        for r in rows:
            created_at = r[12]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "id": r[0],
                    "board_id": r[1],
                    "user_id": r[2],
                    "category": r[3],
                    "title": r[4],
                    "push_count": r[5],
                    "boo_count": r[6],
                    "comment_count": r[7],
                    "tips_total": r[8],
                    "view_count": r[9],
                    "is_pinned": bool(r[10]),
                    "is_hidden": bool(r[11]),
                    "created_at": created_at,
                    "username": r[13],
                    "board_name": r[14],
                    "board_slug": r[15],
                    "net_votes": r[5] - r[6],
                }
            )
        return result
    finally:
        conn.close()


def get_post_by_id(
    post_id: int, increment_view: bool = True, viewer_user_id: Optional[str] = None
) -> Optional[Dict]:
    """獲取文章詳情"""
    conn = get_connection()
    c = conn.cursor()
    try:
        if increment_view:
            c.execute(
                "UPDATE posts SET view_count = view_count + 1 WHERE id = %s", (post_id,)
            )
            conn.commit()

        c.execute(
            """
            SELECT p.id, p.board_id, p.user_id, p.category, p.title, p.content, p.tags,
                   p.push_count, p.boo_count, p.comment_count, p.tips_total, p.view_count,
                   p.payment_tx_hash, p.is_pinned, p.is_hidden, p.created_at, p.updated_at,
                   u.username, b.name as board_name, b.slug as board_slug
            FROM posts p
            LEFT JOIN users u ON p.user_id = u.user_id
            LEFT JOIN boards b ON p.board_id = b.id
            WHERE p.id = %s
        """,
            (post_id,),
        )
        row = c.fetchone()

        if row:
            tags = json.loads(row[6]) if row[6] else []
            created_at = row[15]
            updated_at = row[16]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            if updated_at:
                updated_at = updated_at.strftime("%Y-%m-%d %H:%M:%S")

            post_data = {
                "id": row[0],
                "board_id": row[1],
                "user_id": row[2],
                "category": row[3],
                "title": row[4],
                "content": row[5],
                "tags": tags,
                "push_count": row[7],
                "boo_count": row[8],
                "comment_count": row[9],
                "tips_total": row[10],
                "view_count": row[11],
                "payment_tx_hash": row[12],
                "is_pinned": bool(row[13]),
                "is_hidden": bool(row[14]),
                "created_at": created_at,
                "updated_at": updated_at,
                "username": row[17],
                "board_name": row[18],
                "board_slug": row[19],
                "net_votes": row[7] - row[8],
                "viewer_vote": None,
            }

            # 如果有提供 viewer_user_id，檢查投票狀態
            if viewer_user_id:
                c.execute(
                    """
                    SELECT type FROM forum_comments
                    WHERE post_id = %s AND user_id = %s AND type IN ('push', 'boo')
                    LIMIT 1
                """,
                    (post_id, viewer_user_id),
                )
                vote_row = c.fetchone()
                if vote_row:
                    post_data["viewer_vote"] = vote_row[0]

            return post_data
        return None
    finally:
        conn.close()


def update_post(
    post_id: int,
    user_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
) -> bool:
    """更新文章（只有作者可以更新）"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
        row = c.fetchone()
        if not row or row[0] != user_id:
            return False

        updates = []
        params: List[Any] = []

        # Sanitize UGC(後端縱深防護,防 stored XSS)
        from core.validators.forum_sanitizer import (
            sanitize_category,
            sanitize_post_content,
            sanitize_post_title,
        )

        if title is not None:
            title = sanitize_post_title(title)
        if content is not None:
            content = sanitize_post_content(content)
        if category is not None:
            category = sanitize_category(category)

        candidate_updates = {
            "title": title,
            "content": content,
            "category": category,
        }
        for column, value in candidate_updates.items():
            if value is not None:
                updates.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
                params.append(value)

        if updates:
            updates.append(sql.SQL("updated_at = NOW()"))
            params.append(post_id)
            query = (
                sql.SQL("UPDATE posts SET ")
                + sql.SQL(", ").join(updates)
                + sql.SQL(" WHERE id = %s")
            )
            c.execute(query, params)
            conn.commit()
            return c.rowcount > 0
        return False
    finally:
        conn.close()


def delete_post(post_id: int, user_id: str) -> bool:
    """刪除文章（軟刪除，設為隱藏）"""
    conn = get_connection()
    c = conn.cursor()
    try:
        # 執行軟刪除
        c.execute(
            "UPDATE posts SET is_hidden = 1 WHERE id = %s AND user_id = %s",
            (post_id, user_id),
        )
        success = c.rowcount > 0

        # 如果刪除成功，更新標籤統計與記錄審計日誌
        if success:
            # 1. 更新標籤統計 (Decrement tag counts)
            try:
                # 獲取該文章的標籤
                c.execute("SELECT tag_id FROM post_tags WHERE post_id = %s", (post_id,))
                tag_rows = c.fetchall()
                if tag_rows:
                    for (tag_id,) in tag_rows:
                        # 減少標籤的使用計數
                        c.execute(
                            "UPDATE tags SET post_count = GREATEST(0, post_count - 1) WHERE id = %s",
                            (tag_id,),
                        )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(f"更新標籤統計失敗: {e}")
                # 不影響刪除主流程

            # 2. 記錄審計日誌
            try:
                AuditLogger.log(
                    action="delete_post",
                    user_id=user_id,
                    resource_type="post",
                    resource_id=str(post_id),
                    endpoint="/api/forum/posts/{id}",
                    method="DELETE",
                    success=True,
                    cursor=c,
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                # 審計日誌失敗不應該影響主操作
                logger.warning(f"審計日誌記錄失敗: {e}")

        conn.commit()
        return success
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        # 記錄失敗的審計日誌
        try:
            AuditLogger.log(
                action="delete_post",
                user_id=user_id,
                resource_type="post",
                resource_id=str(post_id),
                endpoint="/api/forum/posts/{id}",
                method="DELETE",
                success=False,
                error_message=str(e),
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as audit_err:
            logger.warning(f"失敗審計日誌記錄失敗: {audit_err}")
        raise
    finally:
        conn.close()


def get_user_posts(user_id: str, limit: int = 20, offset: int = 0) -> List[Dict]:
    """獲取用戶的文章列表"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT p.id, p.board_id, p.category, p.title,
                   p.push_count, p.boo_count, p.comment_count, p.tips_total, p.view_count,
                   p.is_pinned, p.is_hidden, p.created_at,
                   b.name as board_name, b.slug as board_slug
            FROM posts p
            LEFT JOIN boards b ON p.board_id = b.id
            WHERE p.user_id = %s AND p.is_hidden = 0
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """,
            (user_id, limit, offset),
        )
        rows = c.fetchall()

        result = []
        for r in rows:
            created_at = r[11]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "id": r[0],
                    "board_id": r[1],
                    "category": r[2],
                    "title": r[3],
                    "push_count": r[4],
                    "boo_count": r[5],
                    "comment_count": r[6],
                    "tips_total": r[7],
                    "view_count": r[8],
                    "is_pinned": bool(r[9]),
                    "is_hidden": bool(r[10]),
                    "created_at": created_at,
                    "board_name": r[12],
                    "board_slug": r[13],
                    "net_votes": r[4] - r[5],
                }
            )
        return result
    finally:
        conn.close()


# ============================================================================
# 回覆 (Comments)
# ============================================================================


def add_comment(
    post_id: int,
    user_id: str,
    comment_type: str,
    content: Optional[str] = None,
    parent_id: Optional[int] = None,
) -> Dict:
    """
    新增回覆
    comment_type: 'push' / 'boo' / 'comment'
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        # 檢查每日回覆限制（免費會員）- 僅針對實際留言，推噓不計入
        if comment_type == "comment":
            membership = get_user_membership(user_id)
            limits = get_limits()
            is_premium = membership.get("is_premium", False)
            limit = (
                limits["daily_comment_premium"]
                if is_premium
                else limits["daily_comment_free"]
            )

            # 如果有限制，檢查是否超過
            if limit is not None:
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                c.execute(
                    """
                    SELECT comment_count FROM user_daily_comments
                    WHERE user_id = %s AND date = %s
                """,
                    (user_id, today),
                )
                row = c.fetchone()
                current_count = row[0] if row else 0

                if current_count >= limit:
                    return {
                        "success": False,
                        "error": "daily_limit_reached",
                        "limit": limit,
                    }

        # 檢查是否重複推噓 (同一篇文章只能推/噓一次) - 實作 Toggle 邏輯
        if comment_type in ["push", "boo"]:
            c.execute(
                """
                SELECT id, type FROM forum_comments
                WHERE post_id = %s AND user_id = %s AND type IN ('push', 'boo')
            """,
                (post_id, user_id),
            )
            existing_vote = c.fetchone()

            if existing_vote:
                vote_id, v_type = existing_vote

                # 如果點擊的是同一種類型 -> 取消 (Toggle Off)
                if v_type == comment_type:
                    c.execute("DELETE FROM forum_comments WHERE id = %s", (vote_id,))
                    # push/boo 不影響 comment_count，只更新各自的計數
                    if v_type == "push":
                        c.execute(
                            "UPDATE posts SET push_count = GREATEST(0, push_count - 1) WHERE id = %s",
                            (post_id,),
                        )
                    else:
                        c.execute(
                            "UPDATE posts SET boo_count = GREATEST(0, boo_count - 1) WHERE id = %s",
                            (post_id,),
                        )
                    conn.commit()
                    return {"success": True, "action": "cancelled"}

                # 如果點擊的是不同類型 -> 切換 (Switch)
                else:
                    # 先刪除舊的
                    c.execute("DELETE FROM forum_comments WHERE id = %s", (vote_id,))
                    # push/boo 不影響 comment_count，只更新各自的計數
                    if v_type == "push":
                        c.execute(
                            "UPDATE posts SET push_count = GREATEST(0, push_count - 1) WHERE id = %s",
                            (post_id,),
                        )
                    else:
                        c.execute(
                            "UPDATE posts SET boo_count = GREATEST(0, boo_count - 1) WHERE id = %s",
                            (post_id,),
                        )
                    # 接下來會繼續執行下面的 INSERT

        # Sanitize comment content(後端縱深防護,防 stored XSS)
        if content:
            from core.validators.forum_sanitizer import sanitize_comment

            content = sanitize_comment(content)

        # 新增回覆
        c.execute(
            """
            INSERT INTO forum_comments (post_id, user_id, parent_id, type, content, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            RETURNING id
        """,
            (post_id, user_id, parent_id, comment_type, content),
        )

        comment_id = c.fetchone()[0]

        # 更新文章統計
        # 注意：comment_count 只統計真正的留言，push/boo 只更新各自的計數
        if comment_type == "push":
            c.execute(
                "UPDATE posts SET push_count = push_count + 1 WHERE id = %s", (post_id,)
            )
        elif comment_type == "boo":
            c.execute(
                "UPDATE posts SET boo_count = boo_count + 1 WHERE id = %s", (post_id,)
            )
        else:
            c.execute(
                "UPDATE posts SET comment_count = comment_count + 1 WHERE id = %s",
                (post_id,),
            )

        # 更新每日回覆計數 (僅針對實際留言)
        if comment_type == "comment":
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            c.execute(
                """
                INSERT INTO user_daily_comments (user_id, date, comment_count)
                VALUES (%s, %s, 1)
                ON CONFLICT(user_id, date) DO UPDATE SET comment_count = user_daily_comments.comment_count + 1
            """,
                (user_id, today),
            )

        conn.commit()
        return {"success": True, "comment_id": comment_id}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Add comment error: {e}")
        conn.rollback()
        # 原始例外（表名、欄位、constraint）只進 log，不回給前端
        logger.warning("[db] %s failed: %s", __name__, e)
        return {"success": False, "error": "internal_error"}
    finally:
        conn.close()


def get_comments(
    post_id: int, include_hidden: bool = False, limit: int = 50, offset: int = 0
) -> List[Dict]:
    """獲取文章的回覆列表"""
    conn = get_connection()
    c = conn.cursor()
    try:
        query = """
            SELECT c.id, c.post_id, c.user_id, c.parent_id, c.type, c.content,
                   c.is_hidden, c.created_at, u.username
            FROM forum_comments c
            LEFT JOIN users u ON c.user_id = u.user_id
            WHERE c.post_id = %s
        """
        params: List[Any] = [post_id]
        if not include_hidden:
            query += " AND c.is_hidden = 0"
        query += " ORDER BY c.created_at ASC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        c.execute(query, params)
        rows = c.fetchall()

        result = []
        for r in rows:
            created_at = r[7]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                {
                    "id": r[0],
                    "post_id": r[1],
                    "user_id": r[2],
                    "parent_id": r[3],
                    "type": r[4],
                    "content": r[5],
                    "is_hidden": bool(r[6]),
                    "created_at": created_at,
                    "username": r[8],
                }
            )
        return result
    finally:
        conn.close()


def get_daily_comment_count(user_id: str) -> Dict:
    """獲取用戶今日回覆數"""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row = DatabaseBase.query_one(
        "SELECT comment_count FROM user_daily_comments WHERE user_id = %s AND date = %s",
        (user_id, today),
    )
    count = row["comment_count"] if row else 0

    membership = get_user_membership(user_id)
    limits = get_limits()
    is_premium = membership.get("is_premium", False)
    limit = (
        limits["daily_comment_premium"] if is_premium else limits["daily_comment_free"]
    )

    return {
        "count": count,
        "limit": limit,
        "remaining": None if limit is None else max(0, limit - count),
    }


def get_daily_post_count(user_id: str) -> Dict:
    """獲取用戶今日發文數"""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row = DatabaseBase.query_one(
        "SELECT post_count FROM user_daily_posts WHERE user_id = %s AND date = %s",
        (user_id, today),
    )
    count = row["post_count"] if row else 0

    membership = get_user_membership(user_id)
    limits = get_limits()
    is_premium = membership.get("is_premium", False)
    limit = limits["daily_post_premium"] if is_premium else limits["daily_post_free"]

    return {
        "count": count,
        "limit": limit,
        "remaining": None if limit is None else max(0, limit - count),
    }


# ============================================================================
# 打賞 (Tips)
# ============================================================================


def create_tip(
    post_id: int, from_user_id: str, to_user_id: str, amount: float, tx_hash: str
) -> int:
    """創建打賞記錄"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO tips (post_id, from_user_id, to_user_id, amount, tx_hash, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            RETURNING id
        """,
            (post_id, from_user_id, to_user_id, amount, tx_hash),
        )

        tip_id = c.fetchone()[0]

        # 更新文章累計打賞
        c.execute(
            "UPDATE posts SET tips_total = tips_total + %s WHERE id = %s",
            (amount, post_id),
        )

        conn.commit()
        return tip_id
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Create tip error: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def get_tips_sent(user_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
    """獲取用戶送出的打賞記錄"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT t.id, t.post_id, t.to_user_id, t.amount, t.tx_hash, t.created_at,
                   p.title, u.username as to_username
            FROM tips t
            LEFT JOIN posts p ON t.post_id = p.id
            LEFT JOIN users u ON t.to_user_id = u.user_id
            WHERE t.from_user_id = %s
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """,
            (user_id, limit, offset),
        )
        rows = c.fetchall()

        result = []
        for r in rows:
            created_at = r[5]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                annotate_tip_row(
                    {
                        "id": r[0],
                        "post_id": r[1],
                        "to_user_id": r[2],
                        "amount": r[3],
                        "tx_hash": r[4],
                        "created_at": created_at,
                        "post_title": r[6],
                        "to_username": r[7],
                    }
                )
            )
        return result
    finally:
        conn.close()


def get_tips_received(user_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
    """獲取用戶收到的打賞記錄"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT t.id, t.post_id, t.from_user_id, t.amount, t.tx_hash, t.created_at,
                   p.title, u.username as from_username
            FROM tips t
            LEFT JOIN posts p ON t.post_id = p.id
            LEFT JOIN users u ON t.from_user_id = u.user_id
            WHERE t.to_user_id = %s
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """,
            (user_id, limit, offset),
        )
        rows = c.fetchall()

        result = []
        for r in rows:
            created_at = r[5]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            result.append(
                annotate_tip_row(
                    {
                        "id": r[0],
                        "post_id": r[1],
                        "from_user_id": r[2],
                        "amount": r[3],
                        "tx_hash": r[4],
                        "created_at": created_at,
                        "post_title": r[6],
                        "from_username": r[7],
                    }
                )
            )
        return result
    finally:
        conn.close()


def get_tips_total_received(user_id: str) -> float:
    """獲取用戶累計收到的打賞總額（USDC；2026-09-25 前的 TON 打賞不算進來，
    列表裡照樣顯示、標 TON）"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM tips "
            "WHERE to_user_id = %s AND tx_hash LIKE '0x%%'",
            (user_id,),
        )
        return c.fetchone()[0]
    finally:
        conn.close()


# ============================================================================
# 標籤 (Tags)
# ============================================================================


def get_trending_tags(limit: int = 10) -> List[Dict]:
    """從現有可見文章聚合熱門標籤，避免吃到已刪文殘留統計。"""
    results = DatabaseBase.query_all(
        """
        WITH expanded_tags AS (
            SELECT
                p.id AS post_id,
                UPPER(BTRIM(tag_name)) AS name,
                p.created_at
            FROM posts p
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN p.tags IS NULL OR BTRIM(p.tags) = '' THEN '[]'::jsonb
                    ELSE p.tags::jsonb
                END
            ) AS tag_rows(tag_name)
            WHERE p.is_hidden = 0
              AND p.created_at > NOW() - INTERVAL '30 days'
        )
        SELECT
            NULL::integer AS id,
            name,
            COUNT(DISTINCT post_id) AS post_count,
            MAX(created_at) AS last_used_at
        FROM expanded_tags
        WHERE name <> ''
        GROUP BY name
        ORDER BY COUNT(DISTINCT post_id) DESC, MAX(created_at) DESC
        LIMIT %s
    """,
        (limit,),
    )

    # Format datetime fields
    for r in results:
        last_used_at = r.get("last_used_at")
        if last_used_at:
            r["last_used_at"] = last_used_at.strftime("%Y-%m-%d %H:%M:%S")
    return results


def get_posts_by_tag(tag_name: str, limit: int = 20, offset: int = 0) -> List[Dict]:
    """根據標籤獲取文章列表"""
    return get_posts(tag=tag_name, limit=limit, offset=offset)


def search_tags(query: str, limit: int = 10) -> List[Dict]:
    """從現有可見文章搜尋標籤。"""
    return DatabaseBase.query_all(
        """
        WITH expanded_tags AS (
            SELECT
                p.id AS post_id,
                UPPER(BTRIM(tag_name)) AS name,
                p.created_at
            FROM posts p
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN p.tags IS NULL OR BTRIM(p.tags) = '' THEN '[]'::jsonb
                    ELSE p.tags::jsonb
                END
            ) AS tag_rows(tag_name)
            WHERE p.is_hidden = 0
        )
        SELECT
            NULL::integer AS id,
            name,
            COUNT(DISTINCT post_id) AS post_count
        FROM expanded_tags
        WHERE name <> ''
          AND name LIKE %s
        GROUP BY name
        ORDER BY COUNT(DISTINCT post_id) DESC, MAX(created_at) DESC
        LIMIT %s
    """,
        (f"%{query.upper()}%", limit),
    )


# ============================================================================
# 用戶論壇統計
# ============================================================================


def get_user_forum_stats(user_id: str) -> Dict:
    """獲取用戶論壇統計資料"""
    with DatabaseBase() as db:
        # 文章數
        c = db.connection.cursor()
        c.execute(
            "SELECT COUNT(*) FROM posts WHERE user_id = %s AND is_hidden = 0",
            (user_id,),
        )
        post_count = c.fetchone()[0]

        # 回覆數
        c.execute(
            "SELECT COUNT(*) FROM forum_comments WHERE user_id = %s AND is_hidden = 0",
            (user_id,),
        )
        comment_count = c.fetchone()[0]

        # 獲得的推數
        c.execute(
            "SELECT COALESCE(SUM(push_count), 0) FROM posts WHERE user_id = %s",
            (user_id,),
        )
        total_pushes = c.fetchone()[0]

        # 收到的打賞總額
        tips_total = get_tips_total_received(user_id)

        return {
            "post_count": post_count,
            "comment_count": comment_count,
            "total_pushes": total_pushes,
            "tips_received": tips_total,
        }


def get_user_payment_history(
    user_id: str, limit: int = 50, offset: int = 0
) -> List[Dict]:
    """獲取用戶的所有付款記錄（包含發文費和會員費）"""
    # posts 沒有金額欄：這裡帶的是舊 TON 發文費（TON_PRICE_CREATE_POST，預設 0.1），
    # 只給 2026-09-25 前的 TON 發文列用；USDC 發文列由 annotate_payment_row 換成
    # FORUM_POST_FEE_USD。
    from core.config import TON_PAYMENT_PRICES

    post_fee = float(TON_PAYMENT_PRICES.get("create_post", 0.1))
    # 使用 UNION（自動去重）合併發文紀錄和會員購買紀錄
    # 避免同一個 tx_hash 出現兩次
    query = """
        SELECT type, id, title, amount, months, tx_hash, created_at FROM (
            -- 文章支付紀錄
            SELECT
                'post' as type,
                id,
                title,
                %s as amount,
                NULL::integer as months,
                payment_tx_hash as tx_hash,
                created_at
            FROM posts
            WHERE user_id = %s AND payment_tx_hash IS NOT NULL AND payment_tx_hash != 'pro_member_free'

            UNION

            -- 會員購買紀錄
            SELECT
                'membership' as type,
                id,
                'Premium Membership (' || months || ' Month)' as title,
                amount,
                months,
                tx_hash,
                created_at
            FROM membership_payments
            WHERE user_id = %s
        ) AS combined
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """

    results = DatabaseBase.query_all(query, (post_fee, user_id, user_id, limit, offset))

    # Format datetime fields
    for r in results:
        created_at = r.get("created_at")
        if created_at:
            r["created_at"] = created_at.strftime("%Y-%m-%d %H:%M:%S")
        # Rename 'id' to 'ref_id' for clarity
        r["ref_id"] = r.pop("id")
        annotate_payment_row(r)

    return results


_EVM_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADMIN_GRANT_PREFIX = "admin_grant_"


def _usd_anchor_for_months(months: Any) -> Optional[float]:
    """Premium 的 USD 錨定價：年繳走 premium_yearly，其餘按月。"""
    from core.config import PREMIUM_USD_PRICES

    try:
        m = int(months or 0)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    monthly = float(PREMIUM_USD_PRICES.get("premium_monthly", 12.0))
    yearly = float(PREMIUM_USD_PRICES.get("premium_yearly", monthly * 12))
    years, rest = divmod(m, 12)
    return years * yearly + rest * monthly


def annotate_payment_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """付款紀錄補上資產別與顯示金額；admin 授予不是付款。

    背景（2026-09-12 DANNY 截圖）：``membership_payments.amount`` 不管走哪條軌
    都是「TON 定價 × 月數」，前端又一律印 TON，於是 USDC 訂單與 admin 授予都
    顯示成「-1 TON」。payment_orders 沒存最終 tx hash，對不回去，所以用
    tx_hash 的形狀判：``admin_grant_`` 前綴＝授予（無金額）；``0x``+64 hex＝
    EVM（USDC on Base，金額＝USD 錨定 × 月數——穩定幣軌本來就是錨定金額）；
    其餘＝TON（沿用 amount）。

    2026-09-25 起 USDC 訂閱的 amount 記實收美元，但之前的 USDC 列存的仍是
    TON 價、又沒有資產欄可分新舊——所以 EVM 列顯示照舊走錨定價。
    """
    tx = str(row.get("tx_hash") or "")
    amount = row.get("amount")
    try:
        amount_f = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_f = None

    if row.get("type") == "membership":
        if tx.startswith(ADMIN_GRANT_PREFIX):
            row.update(
                kind="admin_grant",
                is_admin_grant=True,
                asset=None,
                rail=None,
                chain=None,
                display_amount=None,
            )
        elif _EVM_TX_RE.match(tx):
            row.update(
                kind="membership",
                is_admin_grant=False,
                asset="USDC",
                rail="evm_usdc",
                chain="base",
                display_amount=_usd_anchor_for_months(row.get("months")),
            )
        else:
            row.update(
                kind="membership",
                is_admin_grant=False,
                asset="TON",
                rail="ton_native",
                chain="ton",
                display_amount=amount_f,
            )
    elif _EVM_TX_RE.match(tx):
        # 2026-09-25 起發文費是 USDC on Base（posts 沒有金額欄，顯示設定的發文費）
        from core.config import FORUM_POST_FEE_USD

        row.update(
            kind="post",
            is_admin_grant=False,
            asset="USDC",
            rail="evm_usdc",
            chain="base",
            display_amount=float(FORUM_POST_FEE_USD),
        )
    else:
        row.update(
            kind="post",
            is_admin_grant=False,
            asset="TON",
            rail="ton_native",
            chain="ton",
            display_amount=amount_f,
        )
    return row


def annotate_tip_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """打賞紀錄補上資產別：0x+64 hex＝USDC on Base（2026-09-25 起），其餘＝舊的 TON。"""
    if _EVM_TX_RE.match(str(row.get("tx_hash") or "")):
        row.update(asset="USDC", chain="base")
    else:
        row.update(asset="TON", chain="ton")
    return row
