"""Google 帳號 ↔ 平台帳號綁定（google_bindings，c051）。比照 telegram_bindings：
一個 Google 帳號只能綁一個平台帳號、一個平台帳號只能綁一個 Google 帳號，換綁＝覆蓋。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .connection import get_connection

logger = logging.getLogger(__name__)


def create_google_binding(
    google_sub: str,
    user_id: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM google_bindings WHERE user_id = %s OR google_sub = %s",
                (user_id, google_sub),
            )
            c.execute(
                """
                INSERT INTO google_bindings (google_sub, user_id, email, display_name, linked_at)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (google_sub, user_id, email, display_name),
            )
        conn.commit()
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("create_google_binding failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    return {
        "google_sub": row[0],
        "user_id": row[1],
        "email": row[2],
        "display_name": row[3],
        "linked_at": row[4].isoformat() if row[4] else None,
        "last_used_at": row[5].isoformat() if row[5] else None,
    }


def get_binding_by_sub(google_sub: str) -> Optional[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT google_sub, user_id, email, display_name, linked_at, last_used_at "
                "FROM google_bindings WHERE google_sub = %s",
                (google_sub,),
            )
            row = c.fetchone()
            return _row_to_dict(row) if row else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("get_binding_by_sub failed: %s", exc)
        return None
    finally:
        conn.close()


def get_binding_by_user_id(user_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT google_sub, user_id, email, display_name, linked_at, last_used_at "
                "FROM google_bindings WHERE user_id = %s",
                (user_id,),
            )
            row = c.fetchone()
            return _row_to_dict(row) if row else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("get_binding_by_user_id failed: %s", exc)
        return None
    finally:
        conn.close()


def update_last_used(google_sub: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE google_bindings SET last_used_at = NOW() WHERE google_sub = %s",
                (google_sub,),
            )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_last_used(google) failed: %s", exc)
        conn.rollback()
    finally:
        conn.close()


def delete_binding_by_user_id(user_id: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM google_bindings WHERE user_id = %s", (user_id,))
            n = c.rowcount
        conn.commit()
        return bool(n)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_google_binding failed: %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()
