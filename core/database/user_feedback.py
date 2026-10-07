import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from .connection import get_connection, get_database_url

logger = logging.getLogger(__name__)

TABLE_NAME = "user_feedback"
LEGACY_TABLE_NAMES = ["user-massage", "user-massege"]
SQLITE_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "local_feedback.sqlite3"


def _can_use_sqlite_fallback() -> bool:
    env = os.getenv("ENVIRONMENT", "development").lower()
    explicit_fallback = (
        os.getenv("FEEDBACK_SQLITE_FALLBACK", "false").lower() == "true"
    )
    return explicit_fallback and env != "production"


def _ensure_postgres_feedback_table(cursor) -> None:
    for legacy in LEGACY_TABLE_NAMES:
        cursor.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('"{legacy}"') IS NOT NULL
                   AND to_regclass('{TABLE_NAME}') IS NULL THEN
                    EXECUTE 'ALTER TABLE "{legacy}" RENAME TO {TABLE_NAME}';
                END IF;
            END $$;
            """
        )
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_feedback_user_id
        ON {TABLE_NAME} (user_id)
        """
    )
    cursor.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_user_feedback_created_at
        ON {TABLE_NAME} (created_at DESC)
        """
    )


def _ensure_sqlite_feedback_table(conn: sqlite3.Connection) -> None:
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for legacy in LEGACY_TABLE_NAMES:
        if legacy in existing_tables and TABLE_NAME not in existing_tables:
            conn.execute(
                f'ALTER TABLE "{legacy}" RENAME TO "{TABLE_NAME}"'
            )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            username TEXT,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _save_user_feedback_sqlite(user_id: str, username: str, message: str) -> None:
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    try:
        _ensure_sqlite_feedback_table(conn)
        conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (user_id, username, message)
            VALUES (?, ?, ?)
            """,
            (user_id, username, message.strip()),
        )
        conn.commit()
        logger.info("User feedback stored in local SQLite fallback: %s", SQLITE_DB_PATH)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_user_feedback(user_id: str, username: str, message: str) -> None:
    """Persist a user-submitted platform feedback message."""
    if not get_database_url():
        if _can_use_sqlite_fallback():
            _save_user_feedback_sqlite(user_id, username, message)
            return
        raise RuntimeError(
            "PostgreSQL feedback storage is not configured. "
            "Set DATABASE_URL or enable FEEDBACK_SQLITE_FALLBACK=true for local testing."
        )

    conn = get_connection()
    c = conn.cursor()
    try:
        _ensure_postgres_feedback_table(c)
        c.execute(
            f"""
            INSERT INTO {TABLE_NAME} (user_id, username, message)
            VALUES (%s, %s, %s)
            """,
            (user_id, username, message.strip()),
        )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("User feedback save error: %s", e)
        conn.rollback()
        if _can_use_sqlite_fallback():
            logger.warning(
                "Falling back to local SQLite for user feedback after DB error: %s", e
            )
            _save_user_feedback_sqlite(user_id, username, message)
            return
        raise
    finally:
        conn.close()
