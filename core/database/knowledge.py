"""LLM Wiki knowledge store (方向③ — Karpathy LLM Wiki knowledge compounding).

Provides:
- save_page(): write a knowledge page (from auto-capture hook or user promote)
- retrieve_relevant(): FTS retrieval on title+body (ts_rank)
- format_for_prompt(): compact block for LLM injection
- touch(): increment access_count on retrieval (popular knowledge surfaces)

Mirrors the proven task_experiences FTS pattern (to_tsvector + GIN + ts_rank).
No vector DB required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from .base import DatabaseBase

logger = logging.getLogger(__name__)

# 啟發式擷取門檻:回應低於此字數不算「有價值的分析」(閒聊/簡短答覆)
MIN_CAPTURE_RESPONSE_CHARS = 200
# 啟發式擷取門檻:工具數低於此不算分析(純文字問答無工具依據)
MIN_CAPTURE_TOOLS = 1


class KnowledgeStore:
    """Read/write knowledge pages (LLM Wiki)."""

    # ── writes ────────────────────────────────────────────────────────────────

    def save_page(
        self,
        owner_user_id: str,
        title: str,
        body: str,
        source_query: Optional[str] = None,
        source_session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        quality_score: float = 0.0,
        promoted: bool = False,
    ) -> Optional[int]:
        """Insert a knowledge page. Returns the page id, or None on failure."""
        if not owner_user_id or not title or not body:
            return None
        try:
            DatabaseBase.execute(
                """
                INSERT INTO knowledge_pages
                    (owner_user_id, title, body, source_query, source_session_id,
                     tags, quality_score, promoted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    owner_user_id,
                    title.strip()[:200],
                    body,
                    source_query,
                    source_session_id,
                    tags or [],
                    quality_score,
                    promoted,
                ),
            )
            logger.info(
                "[KnowledgeStore] saved page for user=%s title=%s",
                owner_user_id,
                title[:50],
            )
            return True
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 — fire-and-forget, 不可擋主流程
            logger.warning("[KnowledgeStore] save_page failed: %s", exc)
            return None

    # ── reads ─────────────────────────────────────────────────────────────────

    def retrieve_relevant(
        self,
        owner_user_id: str,
        query: str,
        limit: int = 3,
    ) -> List[Dict]:
        """FTS retrieval on title+body, filtered by owner. Returns ranked rows.

        Empty list on any failure — never raises.
        """
        if not owner_user_id or not query:
            return []
        try:
            rows = DatabaseBase.query_all(
                """SELECT id, title, body, source_query, tags,
                          quality_score, access_count, promoted, created_at,
                          ts_rank(body_tsv, plainto_tsquery('simple', %s)) AS rank
                   FROM knowledge_pages
                   WHERE owner_user_id = %s
                     AND body_tsv @@ plainto_tsquery('simple', %s)
                   ORDER BY rank DESC, quality_score DESC, access_count DESC, created_at DESC
                   LIMIT %s""",
                (query, owner_user_id, query, limit),
            )
            result = rows or []
            # touch:更新被檢索到的頁面存取計數(熱門知識浮上來)
            if result:
                self._touch(result)
            return result
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.warning("[KnowledgeStore] retrieve_relevant failed: %s", exc)
            return []

    def _touch(self, rows: List[Dict]) -> None:
        """Increment access_count for retrieved pages (best-effort, silent)."""
        try:
            ids = [r["id"] for r in rows if r.get("id") is not None]
            if not ids:
                return
            # 單一 UPDATE 帶 ANY 陣列(避免 N 次 query)
            DatabaseBase.execute(
                "UPDATE knowledge_pages SET access_count = access_count + 1 "
                "WHERE id = ANY(%s)",
                (ids,),
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 — touch 失敗不擋檢索
            logger.debug("[KnowledgeStore] touch failed (non-fatal)")

    def format_for_prompt(self, pages: List[Dict], max_body_chars: int = 1500) -> str:
        """Format retrieved knowledge pages as a compact block for LLM injection.

        長 body 截斷到 max_body_chars(避免單頁吃掉整個 context)。
        """
        if not pages:
            return ""
        lines = ["## 相關知識庫（過去分析,僅供參考,可能過時）"]
        for p in pages:
            title = p.get("title", "").strip()
            body = (p.get("body") or "").strip()
            if len(body) > max_body_chars:
                body = body[:max_body_chars].rsplit("\n", 1)[0] + "…（截斷）"
            promoted = " ⭐使用者收藏" if p.get("promoted") else ""
            lines.append(f"\n### {title}{promoted}\n{body}")
        return "\n".join(lines)

    # ── heuristic capture gate ────────────────────────────────────────────────

    @staticmethod
    def should_capture(
        response: str,
        tools_used: Optional[List[str]] = None,
    ) -> bool:
        """啟發式判斷:這個回應是否值得存成知識頁。

        條件:回應夠長(實質分析,非閒聊) + 用了工具(有資料依據)。
        保守門檻,避免把閒聊/簡短答覆塞進 wiki 製造噪音。
        """
        if not response or len(response.strip()) < MIN_CAPTURE_RESPONSE_CHARS:
            return False
        if not tools_used or len(tools_used) < MIN_CAPTURE_TOOLS:
            return False
        return True
