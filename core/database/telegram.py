"""
Telegram binding database operations.

Manages the link between a Telegram user id and an existing platform
account (users.user_id). The binding is created after the user verifies
a short-lived link token issued from the web app.

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
# Telegram Binding CRUD
# ============================================================================


def create_telegram_binding(
    telegram_id: int,
    user_id: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> bool:
    """Create (or refresh) a Telegram -> user binding.

    If a binding already exists for this ``telegram_id``, it is updated
    in place (re-binding to a new account). If one exists for ``user_id``
    but with a different ``telegram_id``, the old binding is replaced
    because of the UNIQUE(user_id) constraint — a user can only have one
    Telegram account linked at a time.

    Returns True on success, False on failure.
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            # 原子覆蓋（單一交易）：保證「一帳號 ↔ 一 Telegram」雙向唯一。
            # 先清掉所有衝突的舊綁定——同一帳號的舊 Telegram、以及這支 Telegram
            # 的舊帳號——再插入新綁定。
            #
            # 為何不用 INSERT ... ON CONFLICT：衝突可能同時來自 telegram_id(PK)
            # 與 user_id(UNIQUE) 兩個約束。當使用者換綁「另一支新 Telegram」時，
            # ON CONFLICT(telegram_id) 不會命中（新 telegram 尚未存在），INSERT
            # 會先撞 UNIQUE(user_id) 而失敗（清舊列的 DELETE 來不及跑）。
            # 先刪後插則兩種衝突都乾淨處理，且換綁必定成功（= 覆蓋舊的）。
            c.execute(
                "DELETE FROM telegram_bindings "
                "WHERE user_id = %s OR telegram_id = %s",
                (user_id, telegram_id),
            )
            c.execute(
                """
                INSERT INTO telegram_bindings
                    (telegram_id, user_id, username, first_name, linked_at)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (telegram_id, user_id, username, first_name),
            )
        conn.commit()
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("create_telegram_binding failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def get_binding_by_telegram_id(telegram_id: int) -> Optional[dict]:
    """Look up a binding by Telegram numeric user id.

    Returns a dict with keys: telegram_id, user_id, username,
    first_name, linked_at, last_used_at — or None if not bound.
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT telegram_id, user_id, username, first_name,
                       linked_at, last_used_at
                FROM telegram_bindings
                WHERE telegram_id = %s
                """,
                (telegram_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            return {
                "telegram_id": row[0],
                "user_id": row[1],
                "username": row[2],
                "first_name": row[3],
                "linked_at": row[4].isoformat() if row[4] else None,
                "last_used_at": row[5].isoformat() if row[5] else None,
            }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("get_binding_by_telegram_id failed: %s", exc)
        return None
    finally:
        conn.close()


def get_binding_by_user_id(user_id: str) -> Optional[dict]:
    """Look up a binding by platform user_id (wallet address)."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT telegram_id, user_id, username, first_name,
                       linked_at, last_used_at
                FROM telegram_bindings
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            return {
                "telegram_id": row[0],
                "user_id": row[1],
                "username": row[2],
                "first_name": row[3],
                "linked_at": row[4].isoformat() if row[4] else None,
                "last_used_at": row[5].isoformat() if row[5] else None,
            }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("get_binding_by_user_id failed: %s", exc)
        return None
    finally:
        conn.close()


def update_last_used(telegram_id: int) -> None:
    """Mark a binding as recently used (called on each bot interaction)."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE telegram_bindings SET last_used_at = NOW() "
                "WHERE telegram_id = %s",
                (telegram_id,),
            )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("update_last_used failed: %s", exc)
        conn.rollback()
    finally:
        conn.close()


def get_active_session(telegram_id: int) -> Optional[str]:
    """Read the active chat session id for a Telegram binding.

    Deliberately decoupled from get_binding_*: the binding (critical path
    for /status and /chat) must never break just because the optional
    ``active_session_id`` column is missing (e.g. migration c005 not yet
    applied). Any error here — including a missing column — returns None,
    so the caller falls back to the default ``tg:{telegram_id}`` session.
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT active_session_id FROM telegram_bindings "
                "WHERE telegram_id = %s",
                (telegram_id,),
            )
            row = c.fetchone()
            return row[0] if row else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("get_active_session unavailable: %s", exc)
        return None
    finally:
        conn.close()


def set_active_session(telegram_id: int, session_id: Optional[str]) -> bool:
    """Set the active chat session for a Telegram binding.

    Pass ``session_id=None`` to clear it (bot falls back to the default
    rolling session ``tg:{telegram_id}``). Returns True if a row updated.
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE telegram_bindings SET active_session_id = %s "
                "WHERE telegram_id = %s",
                (session_id, telegram_id),
            )
            updated = c.rowcount > 0
        conn.commit()
        return updated
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("set_active_session failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_binding(telegram_id: int) -> bool:
    """Remove a binding by Telegram user id. Returns True if a row was deleted."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM telegram_bindings WHERE telegram_id = %s",
                (telegram_id,),
            )
            deleted = c.rowcount > 0
        conn.commit()
        return deleted
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("delete_binding failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_binding_by_user_id(user_id: str) -> bool:
    """Remove a binding by platform user_id. Returns True if a row was deleted."""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM telegram_bindings WHERE user_id = %s",
                (user_id,),
            )
            deleted = c.rowcount > 0
        conn.commit()
        return deleted
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("delete_binding_by_user_id failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()
