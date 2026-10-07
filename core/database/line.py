"""
LINE binding database operations.

與 ``telegram.py`` 同構——刻意逐一鏡像而非抽共用層：兩邊的 id 型別不同
（LINE 是 33 字元字串，Telegram 是 BIGINT），而且 Telegram 那條路是線上
正在跑的功能，為了 LINE 去動它不划算。

Manages the link between a LINE user id and an existing platform account
(users.user_id). The binding is created after the user verifies a
short-lived link token issued from the web app.

All functions use the legacy psycopg2 sync layer (``get_connection``).
Async callers must wrap them with ``await run_sync(fn, *args)``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# LINE Binding CRUD
# ============================================================================


def create_line_binding(
    line_user_id: str,
    user_id: str,
    display_name: Optional[str] = None,
) -> bool:
    """Create (or refresh) a LINE -> user binding.

    先刪後插（單一交易），理由同 ``create_telegram_binding``：衝突可能同時
    來自 line_user_id(PK) 與 user_id(UNIQUE) 兩個約束，ON CONFLICT 只能挑
    一個，換綁到「另一個 LINE 帳號」時會撞 UNIQUE(user_id) 而失敗。

    Returns True on success, False on failure.
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM line_bindings WHERE user_id = %s OR line_user_id = %s",
                (user_id, line_user_id),
            )
            c.execute(
                """
                INSERT INTO line_bindings
                    (line_user_id, user_id, display_name, linked_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (line_user_id, user_id, display_name),
            )
        conn.commit()
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("create_line_binding failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def get_binding_by_line_user_id(line_user_id: str) -> Optional[dict]:
    """Look up a binding by LINE user id (the ``U…`` string)."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT line_user_id, user_id, display_name, linked_at, last_used_at
                FROM line_bindings
                WHERE line_user_id = %s
                """,
                (line_user_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            return {
                "line_user_id": row[0],
                "user_id": row[1],
                "display_name": row[2],
                "linked_at": row[3].isoformat() if row[3] else None,
                "last_used_at": row[4].isoformat() if row[4] else None,
            }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("get_binding_by_line_user_id failed: %s", exc)
        return None
    finally:
        conn.close()


def get_line_binding_by_user_id(user_id: str) -> Optional[dict]:
    """Look up a binding by platform user_id."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT line_user_id, user_id, display_name, linked_at, last_used_at
                FROM line_bindings
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            return {
                "line_user_id": row[0],
                "user_id": row[1],
                "display_name": row[2],
                "linked_at": row[3].isoformat() if row[3] else None,
                "last_used_at": row[4].isoformat() if row[4] else None,
            }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("get_line_binding_by_user_id failed: %s", exc)
        return None
    finally:
        conn.close()


def update_line_last_used(line_user_id: str) -> None:
    """Mark a binding as recently used (called on each bot interaction)."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE line_bindings SET last_used_at = NOW() WHERE line_user_id = %s",
                (line_user_id,),
            )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("update_line_last_used failed: %s", exc)
        conn.rollback()
    finally:
        conn.close()


def get_line_active_session(line_user_id: str) -> Optional[str]:
    """Read the active chat session id for a LINE binding.

    與 telegram 版同樣刻意與 get_binding_* 解耦：綁定查詢是回覆的關鍵路徑，
    不該因為這個選用欄位讀不到就整個壞掉。任何錯誤一律回 None，呼叫端會
    fallback 到預設的 ``line:{line_user_id}`` session。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT active_session_id FROM line_bindings WHERE line_user_id = %s",
                (line_user_id,),
            )
            row = c.fetchone()
            return row[0] if row else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("get_line_active_session unavailable: %s", exc)
        return None
    finally:
        conn.close()


def set_line_active_session(line_user_id: str, session_id: Optional[str]) -> bool:
    """Set the active chat session for a LINE binding.

    Pass ``session_id=None`` to clear it (falls back to the default rolling
    session ``line:{line_user_id}``). Returns True if a row updated.
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE line_bindings SET active_session_id = %s WHERE line_user_id = %s",
                (session_id, line_user_id),
            )
            updated = c.rowcount > 0
        conn.commit()
        return updated
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("set_line_active_session failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_line_binding(line_user_id: str) -> bool:
    """Remove a binding by LINE user id. Returns True if a row was deleted."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM line_bindings WHERE line_user_id = %s",
                (line_user_id,),
            )
            deleted = c.rowcount > 0
        conn.commit()
        return deleted
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("delete_line_binding failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_line_binding_by_user_id(user_id: str) -> bool:
    """Remove a binding by platform user_id. Returns True if a row was deleted."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM line_bindings WHERE user_id = %s",
                (user_id,),
            )
            deleted = c.rowcount > 0
        conn.commit()
        return deleted
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("delete_line_binding_by_user_id failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()
