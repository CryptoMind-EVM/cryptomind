"""
私訊檢舉（2026-09-29，c061）。

收回會清空原文（messages_repo.recall_message），所以證據改在檢舉當下留：被檢舉那則＋
前 10 則的文字存成 snapshot。快照由這裡從 DB 撈，不收前端傳的內容。只有管理員看得到。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .friends_repo import friends_repo
from .models import DmMessage, DmReport, User
from .session import using_session

logger = logging.getLogger(__name__)

REPORT_REASONS = ("scam", "harassment", "spam", "other")
SNAPSHOT_BEFORE = 10  # 被檢舉那則之前再帶幾則當上下文


def _iso(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


def _report_dict(r: DmReport, usernames: Optional[dict] = None) -> dict:
    names = usernames or {}
    return {
        "id": r.id,
        "reporter_user_id": r.reporter_user_id,
        "reporter_username": names.get(r.reporter_user_id),
        "reported_user_id": r.reported_user_id,
        "reported_username": names.get(r.reported_user_id),
        "conversation_id": r.conversation_id,
        "message_id": r.message_id,
        "reason": r.reason,
        "note": r.note,
        "snapshot": r.snapshot,
        "status": r.status,
        "admin_note": r.admin_note,
        "risk_score": r.risk_score,
        "risk_category": r.risk_category,
        "resolved_by": r.resolved_by,
        "resolved_at": _iso(r.resolved_at),
        "created_at": _iso(r.created_at),
    }


class DmReportsRepository:
    async def report_message(
        self,
        message_id: int,
        reporter_user_id: str,
        reason: str,
        note: Optional[str],
        *,
        block: bool = False,
        session: AsyncSession | None = None,
    ) -> dict:
        """檢舉對方傳的一則訊息。成功回 {"success", "report", "blocked"}。"""
        if reason not in REPORT_REASONS:
            return {"success": False, "error": "invalid_reason"}
        async with using_session(session) as s:
            msg = (
                await s.execute(select(DmMessage).where(DmMessage.id == message_id))
            ).scalar_one_or_none()
            # 不是參與者就當不存在，不洩漏 id 是否存在
            if msg is None or reporter_user_id not in (
                msg.from_user_id,
                msg.to_user_id,
            ):
                return {"success": False, "error": "message_not_found"}
            if msg.from_user_id == reporter_user_id:
                return {"success": False, "error": "cannot_report_own"}

            # 快照不排除檢舉人「為我刪除」的訊息：只有管理員看得到，上下文要完整
            rows = (
                await s.execute(
                    select(
                        DmMessage.id,
                        DmMessage.from_user_id,
                        DmMessage.content,
                        DmMessage.message_type,
                        DmMessage.created_at,
                    )
                    .where(
                        DmMessage.conversation_id == msg.conversation_id,
                        DmMessage.id <= msg.id,
                    )
                    .order_by(DmMessage.id.desc())
                    .limit(SNAPSHOT_BEFORE + 1)
                )
            ).all()
            snapshot = [
                {
                    "id": r[0],
                    "from_user_id": r[1],
                    "content": r[2],
                    "message_type": r[3],
                    "created_at": _iso(r[4]),
                }
                for r in reversed(rows)
            ]

            inserted = (
                await s.execute(
                    pg_insert(DmReport)
                    .values(
                        reporter_user_id=reporter_user_id,
                        reported_user_id=msg.from_user_id,
                        conversation_id=msg.conversation_id,
                        message_id=msg.id,
                        reason=reason,
                        note=note or None,
                        snapshot=snapshot,
                    )
                    .on_conflict_do_nothing(constraint="uq_dm_report")
                    .returning(DmReport.id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                return {"success": False, "error": "already_reported"}

            # 順便封鎖放在 savepoint 裡：封鎖失敗只記 log，檢舉（證據）照樣存
            blocked = False
            if block:
                try:
                    async with s.begin_nested():
                        result = await friends_repo.block_user(
                            reporter_user_id, msg.from_user_id, session=s
                        )
                    blocked = bool(result.get("success"))
                except Exception as e:  # noqa: BLE001 — 封鎖是附帶的，不能拖垮檢舉
                    logger.warning("檢舉後順便封鎖失敗（檢舉已存）: %s", e)

            report = (
                await s.execute(select(DmReport).where(DmReport.id == inserted))
            ).scalar_one()
            return {"success": True, "report": _report_dict(report), "blocked": blocked}

    async def list_reports(
        self,
        status: str = "pending",
        page: int = 1,
        limit: int = 20,
        session: AsyncSession | None = None,
    ) -> dict:
        """管理員看的列表（風險分數高的在前、沒分數的排後面，同分新的在前），附雙方帳號名"""
        return await list_snapshot_reports(
            DmReport, _report_dict, status, page, limit, session=session
        )

    async def resolve_report(
        self,
        report_id: int,
        status: str,
        admin_note: Optional[str],
        admin_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        """標記已處理（resolved）或不成立（dismissed）；只能處理 pending 的"""
        return await resolve_snapshot_report(
            DmReport,
            _report_dict,
            report_id,
            status,
            admin_note,
            admin_user_id,
            session=session,
        )


# ── 私訊與群組檢舉共用（群組在 group_reports_repo，c066）──────────────────────


async def list_snapshot_reports(
    model,
    to_dict,
    status: str,
    page: int,
    limit: int,
    extra_user_ids=lambda r: (),
    session: AsyncSession | None = None,
) -> dict:
    """風險分數高的在前、沒分數的排後面，同分新的在前；names 涵蓋檢舉人、被檢舉人與 extra_user_ids"""
    async with using_session(session) as s:
        total = (
            await s.execute(
                select(func.count()).select_from(model).where(model.status == status)
            )
        ).scalar_one()
        reports = (
            (
                await s.execute(
                    select(model)
                    .where(model.status == status)
                    .order_by(
                        model.risk_score.desc().nulls_last(),
                        model.created_at.desc(),
                        model.id.desc(),
                    )
                    .offset((page - 1) * limit)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        ids = set()
        for r in reports:
            ids |= {r.reporter_user_id, r.reported_user_id, *extra_user_ids(r)}
        ids.discard(None)
        names = {}
        if ids:
            names = dict(
                (
                    await s.execute(
                        select(User.user_id, User.username).where(User.user_id.in_(ids))
                    )
                ).all()
            )
        return {
            "reports": [to_dict(r, names) for r in reports],
            "total": total,
            "page": page,
            "limit": limit,
        }


async def resolve_snapshot_report(
    model,
    to_dict,
    report_id: int,
    status: str,
    admin_note: Optional[str],
    admin_user_id: str,
    session: AsyncSession | None = None,
) -> dict:
    if status not in ("resolved", "dismissed"):
        return {"success": False, "error": "invalid_status"}
    async with using_session(session) as s:
        report = (
            await s.execute(
                select(model).where(model.id == report_id).with_for_update()
            )
        ).scalar_one_or_none()
        if report is None:
            return {"success": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"success": False, "error": "already_resolved"}
        report.status = status
        report.admin_note = admin_note or None
        report.resolved_by = admin_user_id
        report.resolved_at = datetime.now(timezone.utc)
        await s.flush()
        return {"success": True, "report": to_dict(report)}


dm_reports_repo = DmReportsRepository()
