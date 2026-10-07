"""
用戶分析偏好資料庫操作
包含：查詢、新增/更新、刪除 user_analysis_preferences

會員等級：free / premium
- free: 免費用戶（只能使用預設 prompt + 預設工具）
- premium: 付費會員（可自訂 system prompt + 可啟用/關閉個別工具）
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .base import DatabaseBase

logger = logging.getLogger(__name__)


def get_all_preferences(user_id: str) -> List[Dict[str, Any]]:
    """
    獲取用戶所有 agent 的分析偏好。

    Returns:
        List of dicts with keys: agent_id, system_prompt, enabled_tools, updated_at
    """
    rows = DatabaseBase.query_all(
        """
        SELECT agent_id, system_prompt, enabled_tools, updated_at
        FROM user_analysis_preferences
        WHERE user_id = %s
        ORDER BY agent_id
        """,
        (user_id,),
    )
    return rows


def upsert_preference(
    user_id: str,
    agent_id: str,
    system_prompt: Optional[str],
    enabled_tools: Optional[List[str]],
) -> Dict[str, Any]:
    """
    新增或更新用戶某個 agent 的分析偏好。

    Returns:
        Dict with the upserted row: agent_id, system_prompt, enabled_tools, updated_at
    """
    with DatabaseBase() as db:
        cursor = db.connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO user_analysis_preferences (user_id, agent_id, system_prompt, enabled_tools, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (user_id, agent_id)
                DO UPDATE SET
                    system_prompt = EXCLUDED.system_prompt,
                    enabled_tools = EXCLUDED.enabled_tools,
                    updated_at = NOW()
                RETURNING agent_id, system_prompt, enabled_tools, updated_at
                """,
                (user_id, agent_id, system_prompt, enabled_tools),
            )
            row = cursor.fetchone()
            db.connection.commit()
            if row is None:
                return {}
            cols = [desc[0] for desc in cursor.description]
            return dict(zip(cols, row))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            db.connection.rollback()
            raise
        finally:
            cursor.close()


def delete_preference(user_id: str, agent_id: str) -> bool:
    """
    刪除用戶某個 agent 的分析偏好。

    Returns:
        True if a row was deleted, False otherwise.
    """
    affected = DatabaseBase.execute(
        """
        DELETE FROM user_analysis_preferences
        WHERE user_id = %s AND agent_id = %s
        """,
        (user_id, agent_id),
    )
    return affected > 0
