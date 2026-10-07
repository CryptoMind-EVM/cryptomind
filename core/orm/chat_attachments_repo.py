"""Async ORM repository for chat attachments（vision Phase 2，c040/c041）.

設計：docs/plans/2026-08-30-vision-image-storage-design.md
- 壓縮圖（前端 ≤2048px/JPEG，通常 200-800KB）bytea 直存 PostgreSQL，
  零新外部服務、零憑證。
- 寫入走 ORM unit-of-work（db.add）；讀取走 db.get（PK 直取）＋
  Python 端 ownership 檢查——查無或非本人一律回 None，API 層 404，
  不洩漏附件存在性（防 IDOR）。
- 刪除對話的附件連動由 DB 層 FK ON DELETE CASCADE（c041）完成，應用層
  零代碼；30 天保留由每日清理任務呼叫 purge_expired()。
- 回滾：VISION_STORAGE_ENABLED=false 停寫入（讀取保留），表可留。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ChatAttachment

RETENTION_DAYS = 30


class ChatAttachmentsRepository:
    """Async ORM repository for chat_attachments."""

    async def create(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        session_id: str,
        mime: str,
        data: bytes,
    ) -> ChatAttachment:
        attachment = ChatAttachment(
            user_id=user_id,
            session_id=session_id,
            mime=mime,
            size_bytes=len(data),
            data=data,
        )
        db.add(attachment)
        await db.flush()
        await db.refresh(attachment)
        return attachment

    async def get_owned(
        self, db: AsyncSession, attachment_id: int, user_id: str
    ) -> Optional[ChatAttachment]:
        """PK 直取＋Python 端 ownership 檢查（他人或不存在一律 None，防 IDOR）。"""
        attachment = await db.get(ChatAttachment, attachment_id)
        if attachment is None or attachment.user_id != user_id:
            return None
        return attachment

    async def purge_expired(
        self, db: AsyncSession, retention_days: int = RETENTION_DAYS
    ) -> int:
        """刪除超過保留期的附件（每日清理任務用）；回傳刪除列數。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await db.execute(
            sa_delete(ChatAttachment).where(ChatAttachment.created_at < cutoff)
        )
        return int(result.rowcount or 0)


chat_attachments_repo = ChatAttachmentsRepository()


async def chat_attachments_cleanup_task() -> None:
    """每日 03:30 UTC 清理超過保留期（30 天）的對話附圖。

    由 api/lifespan.py 以 asyncio.create_task 掛載
    （沿用 core.audit.audit_log_cleanup_task 模式；錯 30 分避開 audit 清理高峰）。
    """
    import asyncio
    import logging

    logger = logging.getLogger(__name__)

    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=3, minute=30, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            from sqlalchemy.exc import SQLAlchemyError

            from .session import using_session

            async with using_session() as db:
                deleted = await chat_attachments_repo.purge_expired(db)
            if deleted:
                logger.info(
                    "Chat attachments cleanup: purged %d expired (>%dd)",
                    deleted,
                    RETENTION_DAYS,
                )
        except SQLAlchemyError as exc:
            logger.warning("Chat attachments cleanup failed: %s", exc)
