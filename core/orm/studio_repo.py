"""提案工作台 repository（design 2026-08-16）。

不變量：
- ``draft_versions`` 只插入、不更新不刪除（硬邊界 #6）
- 版號由 repo 原子配置（SELECT ... FOR UPDATE 防併發跳號）
- 配額：每人 20 份草稿、每稿 200 版
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime as _dt
from datetime import timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.orm.models import DraftVersion, ProposalDraft

logger = logging.getLogger(__name__)

MAX_DRAFTS_PER_USER = 20
MAX_VERSIONS_PER_DRAFT = 200
_VALID_SOURCES = ("human", "ai_applied", "autosave")


def new_draft_id() -> str:
    return f"pd_{secrets.token_urlsafe(12)}"


def new_version_id() -> str:
    return f"dv_{secrets.token_urlsafe(12)}"


class StudioRepo:
    """草稿與版本的資料存取（皆以 user_id 隔離，ownership 由 caller 傳入）。"""

    async def list_drafts(self, db: AsyncSession, user_id: str) -> list[ProposalDraft]:
        stmt = (
            select(ProposalDraft)
            .where(ProposalDraft.user_id == user_id)
            .order_by(ProposalDraft.updated_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_draft(
        self, db: AsyncSession, user_id: str, draft_id: str
    ) -> Optional[ProposalDraft]:
        return await db.get(ProposalDraft, draft_id) if draft_id else None

    async def create_draft(
        self,
        db: AsyncSession,
        user_id: str,
        title: str,
        cause: Optional[str] = None,
        target_usd: Optional[float] = None,
        initial_content: str = "",
    ) -> ProposalDraft:
        count = await self.count_drafts(db, user_id)
        if count >= MAX_DRAFTS_PER_USER:
            raise QuotaExceeded(f"draft limit ({MAX_DRAFTS_PER_USER}) reached")
        draft = ProposalDraft(
            draft_id=new_draft_id(),
            user_id=user_id,
            title=title,
            cause=cause,
            target_usd=target_usd,
            status="draft",
            current_version_no=1,
        )
        db.add(draft)
        # 先 flush 落 draft 列：無 relationship() 關聯時 UoW 不保證插入順序，
        # 版本的 FK（draft_id）會在 draft 插入前觸發 ForeignKeyViolation（實測 2026-08-16）
        await db.flush()
        db.add(
            DraftVersion(
                version_id=new_version_id(),
                draft_id=draft.draft_id,
                version_no=1,
                content_md=initial_content or "",
                change_summary=None,
                source="human",
                char_delta=len(initial_content or ""),
            )
        )
        await db.commit()
        return draft

    async def count_drafts(self, db: AsyncSession, user_id: str) -> int:
        result = await db.execute(
            select(func.count()).select_from(ProposalDraft).where(
                ProposalDraft.user_id == user_id
            )
        )
        return int(result.scalar() or 0)

    async def update_draft_meta(
        self,
        db: AsyncSession,
        draft: ProposalDraft,
        *,
        title: Optional[str] = None,
        cause: Optional[str] = None,
        target_usd: Optional[float] = None,
        status: Optional[str] = None,
    ) -> ProposalDraft:
        if title is not None:
            draft.title = title
        if cause is not None:
            draft.cause = cause
        if target_usd is not None:
            draft.target_usd = target_usd
        if status is not None:
            draft.status = status
        draft.updated_at = _dt.now(timezone.utc)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise DuplicateTitle("draft title already exists")
        return draft

    async def update_references(
        self,
        db: AsyncSession,
        draft: ProposalDraft,
        *,
        references: list,
    ) -> ProposalDraft:
        """Discover→Studio 參考動線（design 2026-08-17 §Phase 2）。

        references 為可整體替換的 list（路由層已做上限/冪等/截斷）；
        空值統一存 []，避免 NULL 與 [] 兩種空態並存。
        """
        draft.references_json = list(references)
        draft.updated_at = _dt.now(timezone.utc)
        await db.commit()
        return draft

    async def delete_draft(self, db: AsyncSession, draft: ProposalDraft) -> None:
        await db.execute(
            delete(DraftVersion).where(DraftVersion.draft_id == draft.draft_id)
        )
        await db.delete(draft)
        await db.commit()

    # ── 版本（不可變）─────────────────────────────────────────────

    async def get_current_version(
        self, db: AsyncSession, draft_id: str
    ) -> Optional[DraftVersion]:
        stmt = (
            select(DraftVersion)
            .where(DraftVersion.draft_id == draft_id)
            .order_by(DraftVersion.version_no.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def add_version(
        self,
        db: AsyncSession,
        draft: ProposalDraft,
        content_md: str,
        change_summary: Optional[str],
        source: str,
        telemetry: Optional[dict] = None,
    ) -> DraftVersion:
        if source not in _VALID_SOURCES:
            raise ValueError(f"invalid source: {source}")
        # 原子配置版號：鎖草稿列（併發存檔不跳號）
        await db.execute(
            select(ProposalDraft)
            .where(ProposalDraft.draft_id == draft.draft_id)
            .with_for_update()
        )
        latest = await self.get_current_version(db, draft.draft_id)
        next_no = (latest.version_no + 1) if latest else 1
        if next_no > MAX_VERSIONS_PER_DRAFT:
            raise QuotaExceeded(f"version limit ({MAX_VERSIONS_PER_DRAFT}) reached")
        prev_len = len(latest.content_md) if latest else 0
        version = DraftVersion(
            version_id=new_version_id(),
            draft_id=draft.draft_id,
            version_no=next_no,
            content_md=content_md,
            change_summary=change_summary,
            source=source,
            char_delta=len(content_md) - prev_len,
            typed_chars=int((telemetry or {}).get("typed_chars") or 0),
            paste_events=int((telemetry or {}).get("paste_events") or 0),
            pasted_chars=int((telemetry or {}).get("pasted_chars") or 0),
            edit_seconds=int((telemetry or {}).get("edit_seconds") or 0),
        )
        db.add(version)
        draft.current_version_no = next_no
        draft.updated_at = _dt.now(timezone.utc)
        await db.commit()
        return version

    async def list_versions(
        self, db: AsyncSession, draft_id: str
    ) -> list[DraftVersion]:
        stmt = (
            select(
                DraftVersion.version_no,
                DraftVersion.change_summary,
                DraftVersion.source,
                DraftVersion.char_delta,
                DraftVersion.typed_chars,
                DraftVersion.paste_events,
                DraftVersion.pasted_chars,
                DraftVersion.edit_seconds,
                DraftVersion.created_at,
            )
            .where(DraftVersion.draft_id == draft_id)
            .order_by(DraftVersion.version_no.desc())
        )
        result = await db.execute(stmt)
        return list(result.all())

    async def get_version(
        self, db: AsyncSession, draft_id: str, version_no: int
    ) -> Optional[DraftVersion]:
        stmt = (
            select(DraftVersion)
            .where(DraftVersion.draft_id == draft_id)
            .where(DraftVersion.version_no == version_no)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


class QuotaExceeded(Exception):
    pass


class DuplicateTitle(Exception):
    pass


studio_repo = StudioRepo()
