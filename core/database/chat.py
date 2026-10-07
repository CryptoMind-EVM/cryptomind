"""
對話歷史相關資料庫操作
包含：對話會話管理、對話訊息
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional

import psycopg2.errors

from .connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# 對話會話 (Sessions)
# ============================================================================


def create_session(
    session_id: str, title: str = "New Chat", user_id: str = "local_user"
):
    """創建新對話會話。

    Raises:
        Exception: 連線失敗或 INSERT 失敗時重新拋出，讓上層 endpoint 回 503
            而不是默默回 200 + 假的 session_id（過去 except 吞掉例外會造成
            前端拿到 session_id 但後續訊息找不到 session 的隱形 bug）。
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO sessions (session_id, user_id, title, is_pinned, created_at, updated_at)
            VALUES (%s, %s, %s, 0, NOW(), NOW())
        """,
            (session_id, user_id, title),
        )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except psycopg2.errors.UniqueViolation:
        # session 已存在：對 ensure_session 是正常路徑（冪等確保），不留
        # error 級日誌——修復前每則 bot 訊息都炸一行噪音。仍 re-raise，
        # 「已存在」算不算失敗由呼叫端決定（web 端當失敗回 503）。
        if conn is not None:
            try:
                conn.rollback()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
        raise
    except Exception as e:
        logger.error(f"Session create error: {e}")
        if conn is not None:
            try:
                conn.rollback()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
        raise  # 重新拋出：caller（endpoint）必須知道 INSERT 失敗才能回 503
    finally:
        if conn is not None:
            conn.close()


def ensure_session(session_id: str, title: str, user_id: str):
    """冪等地確保 session 存在——「已存在」（UniqueViolation）視為成功。

    bot 聊天管線（Telegram／LINE）每則訊息都會確保預設 session 在：裸
    INSERT 的 :func:`create_session` 在第二則訊息就撞 ``sessions_pkey``
    （2026-09-09 LINE 首位真人使用者踩爆；Telegram 路徑此前無人生產使用）。

    只攔 UniqueViolation——其他 DB 錯誤照原樣傳播（對齊 create_session
    「失敗必須讓上層知道」的約定）。既存列不動：標題與 ``updated_at``
    由 ``save_chat_message`` 的 upsert 負責，這裡動到反而會蓋掉第一則
    訊息寫入的標題。
    """
    try:
        create_session(session_id, title, user_id)
    except psycopg2.errors.UniqueViolation:
        return


def update_session_title(session_id: str, title: str):
    """更新對話標題"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE sessions SET title = %s, updated_at = NOW() WHERE session_id = %s",
            (title, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def toggle_session_pin(session_id: str, is_pinned: bool):
    """切換對話置頂狀態。

    置頂接在同一個人置頂清單的最後面（pin_order = 最大值 + 1）；本來就置頂的保留原順序，
    重按不會被移到最後。取消置頂 pin_order 設 NULL。擁有權由呼叫端檢查。
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        if is_pinned:
            c.execute(
                """
                UPDATE sessions SET
                    is_pinned = 1,
                    pin_order = CASE
                        WHEN is_pinned = 1 AND pin_order IS NOT NULL THEN pin_order
                        ELSE (
                            SELECT COALESCE(MAX(p.pin_order), -1) + 1
                            FROM sessions p
                            WHERE p.user_id = sessions.user_id AND p.is_pinned = 1
                        )
                    END
                WHERE session_id = %s
                """,
                (session_id,),
            )
        else:
            c.execute(
                "UPDATE sessions SET is_pinned = 0, pin_order = NULL WHERE session_id = %s",
                (session_id,),
            )
        conn.commit()
    finally:
        conn.close()


def reorder_pinned_sessions(user_id: str, session_ids: List[str]) -> None:
    """拖曳排序置頂對話（寬鬆）：只動這個人、目前置頂的；有列到的照給的順序排成 0..k-1，
    沒列到的置頂保持原本相對順序接在後面；別人的、沒置頂的、不認得的 id 直接略過。"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT session_id FROM sessions
            WHERE user_id = %s AND is_pinned = 1
            ORDER BY pin_order ASC NULLS LAST, updated_at DESC
            FOR UPDATE
            """,
            (user_id,),
        )
        pinned = [row[0] for row in c.fetchall()]
        pinned_set = set(pinned)
        listed = [sid for sid in dict.fromkeys(session_ids) if sid in pinned_set]
        listed_set = set(listed)
        ordered = listed + [sid for sid in pinned if sid not in listed_set]
        if ordered:
            c.execute(
                """
                UPDATE sessions s SET pin_order = v.ord
                FROM unnest(%s::text[], %s::int[]) AS v(sid, ord)
                WHERE s.session_id = v.sid AND s.user_id = %s AND s.is_pinned = 1
                """,
                (ordered, list(range(len(ordered))), user_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_sessions(
    user_id: str = "local_user", limit: int = 20, offset: int = 0
) -> List[Dict]:
    """獲取用戶的對話列表（置頂優先，置頂的照拖曳順序 pin_order）。

    has_messages：前端開聊天頁會清掉多餘的空「New Chat」——標題只在第一則
    使用者訊息才會換掉，光看標題會連有內容的對話一起刪。
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT s.session_id, s.title, s.created_at, s.updated_at, s.is_pinned,
                   EXISTS (
                       SELECT 1 FROM conversation_history h
                       WHERE h.session_id = s.session_id
                   ) AS has_messages,
                   s.pin_order
            FROM sessions s
            WHERE s.user_id = %s
            ORDER BY s.is_pinned DESC, s.pin_order ASC NULLS LAST, s.updated_at DESC
            LIMIT %s OFFSET %s
        """,
            (user_id, limit, offset),
        )
        rows = c.fetchall()

        sessions = []
        for row in rows:
            created_at = row[2]
            updated_at = row[3]
            if created_at:
                created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
            if updated_at:
                updated_at = updated_at.strftime("%Y-%m-%d %H:%M:%S")
            sessions.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "is_pinned": bool(row[4]),
                    "has_messages": bool(row[5]),
                    "pin_order": row[6],
                }
            )
        return sessions
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Session list error: {e}")
        return []
    finally:
        conn.close()


def delete_session(session_id: str):
    """刪除對話會話及其歷史"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
        c.execute(
            "DELETE FROM conversation_history WHERE session_id = %s", (session_id,)
        )
        conn.commit()
    finally:
        conn.close()


def get_session_owner(session_id: str) -> Optional[str]:
    """session 的擁有者；還沒建立（第一次發訊息才建）回 None。"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT user_id FROM sessions WHERE session_id = %s", (session_id,))
        row = c.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def check_session_ownership(session_id: str, user_id: str) -> bool:
    """Check if a session belongs to a user (single query instead of fetching all sessions)."""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT 1 FROM sessions WHERE session_id = %s AND user_id = %s",
            (session_id, user_id),
        )
        return c.fetchone() is not None
    finally:
        conn.close()


# ============================================================================
# 對話訊息 (Chat Messages)
# ============================================================================

# 思考內容存進 metadata 的長度上限（2026-09-07 DANNY 回報「思考過程 F5 後
# 就不見了」）。串流當下的 reasoning 只活在前端變數裡，從來沒進過 DB，重整
# 必然蒸發。存起來即可還原，但推理模型一次能吐幾萬字——全存會把
# conversation_history.metadata 撐大、拖慢歷史載入。前端那塊是預設收合的
# 閱讀輔助，超過就截斷，答案本身（content）不受影響。
MAX_STORED_REASONING_CHARS = 8000


def clip_reasoning(text: Optional[str]) -> Optional[str]:
    """把思考內容裁到可存長度；空字串一律回 None（metadata 不留空鍵）。

    截斷標記用語言中性的 `[…]`，免得存進 DB 的字串綁死某一種語言
    （同一段歷史會被四種語系的使用者讀到）。
    """
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) <= MAX_STORED_REASONING_CHARS:
        return text
    return text[:MAX_STORED_REASONING_CHARS] + "\n\n[…]"


def save_chat_message(
    role: str,
    content: str,
    session_id: str,
    user_id: Optional[str] = "local_user",
    metadata: Optional[Dict] = None,
):
    """保存對話訊息，並自動更新 session 的 updated_at 和標題"""
    # P1: session_id is required — reject "default" to prevent cross-user data bleed
    if not session_id or session_id == "default":
        raise ValueError(
            "session_id is required and cannot be 'default'. "
            "Each user/conversation must use a unique session_id."
        )
    conn = get_connection()
    c = conn.cursor()
    try:
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        c.execute(
            """
            INSERT INTO conversation_history (session_id, user_id, role, content, metadata, timestamp)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """,
            (session_id, user_id, role, content, metadata_json),
        )

        title = content[:30] + "..." if len(content) > 30 else content
        if role == "assistant":
            title = "AI Analysis"

        c.execute(
            """
            INSERT INTO sessions (session_id, user_id, title, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (session_id) DO UPDATE SET
                updated_at = NOW(),
                title = CASE
                    WHEN sessions.title = 'New Chat' AND %s = 'user' THEN EXCLUDED.title
                    ELSE sessions.title
                END
            """,
            (session_id, user_id, title, role),
        )

        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Chat save error: {e}")
        conn.rollback()
    finally:
        conn.close()


def find_turn(session_id: str, question: str, scan: int = 400) -> Optional[Dict]:
    """某個對話裡「問了 question 的最近一輪」：{question, answer}，找不到回 None。

    分享用：答案一律由伺服器從資料庫取，不接受前端傳來的文字（公開頁寫著「AI 的回答」，
    內容不能是使用者自己編的）。比對用的是清理過的文字（空白、控制字元壓平），
    同一句問過多次取最近一次；那一輪的答案是它之後、下一則使用者訊息之前的第一則 assistant 訊息。
    """
    from core.answer_share import clean_question

    if not session_id or session_id == "default":
        return None
    wanted = clean_question(question)
    if not wanted:
        return None
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT role, content FROM conversation_history
            WHERE session_id = %s ORDER BY id DESC LIMIT %s
            """,
            (session_id, scan),
        )
        rows = list(reversed(c.fetchall()))  # ASC
    finally:
        conn.close()
    for i in range(len(rows) - 1, -1, -1):
        role, content = rows[i]
        if role != "user" or clean_question(content) != wanted:
            continue
        for next_role, next_content in rows[i + 1 :]:
            if next_role == "user":
                break
            if next_role == "assistant" and (next_content or "").strip():
                return {"question": content, "answer": next_content}
        return None  # 問過但還沒有（或沒存到）答案
    return None


def get_chat_history(
    session_id: str,
    limit: int = 20,
    before_timestamp: Optional[str] = None,
) -> List[Dict]:
    """獲取對話歷史（最新 limit 條，ASC 排序）。

    before_timestamp: 若提供，只返回比此時間更早的訊息（向上捲動載入舊訊息用）。
    """
    # P1: session_id is required — reject "default" to prevent cross-user data bleed
    if not session_id or session_id == "default":
        raise ValueError(
            "session_id is required and cannot be 'default'. "
            "Each user/conversation must use a unique session_id."
        )
    conn = get_connection()
    c = conn.cursor()
    try:
        if before_timestamp:
            c.execute(
                """
                SELECT role, content, metadata, timestamp
                FROM conversation_history
                WHERE session_id = %s AND timestamp < %s
                ORDER BY timestamp DESC
                LIMIT %s
            """,
                (session_id, before_timestamp, limit),
            )
        else:
            c.execute(
                """
                SELECT role, content, metadata, timestamp
                FROM conversation_history
                WHERE session_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """,
                (session_id, limit),
            )

        rows = list(reversed(c.fetchall()))  # DESC 取到後反轉成 ASC

        history = []
        for row in rows:
            metadata = json.loads(row[2]) if row[2] else None
            timestamp = row[3]
            if timestamp:
                timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            history.append(
                {
                    "role": row[0],
                    "content": row[1],
                    "metadata": metadata,
                    "timestamp": timestamp,
                }
            )
        return history
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Chat history read error: {e}")
        return []
    finally:
        conn.close()


def clear_chat_history(session_id: str):
    """清除特定 session 的對話歷史"""
    # P1: session_id is required — reject "default" to prevent accidental data deletion
    if not session_id or session_id == "default":
        raise ValueError(
            "session_id is required and cannot be 'default'. "
            "Use a specific session_id to clear history."
        )
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "DELETE FROM conversation_history WHERE session_id = %s", (session_id,)
        )
        conn.commit()
    finally:
        conn.close()


def save_codebook_feedback(codebook_entry_id: str, user_id: str, score: int):
    """儲存 codebook 分析品質回饋"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO codebook_feedback (codebook_entry_id, user_id, score)
            VALUES (%s, %s, %s)
            """,
            (codebook_entry_id, user_id, score),
        )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Codebook feedback save error: {e}")
        conn.rollback()
    finally:
        conn.close()
