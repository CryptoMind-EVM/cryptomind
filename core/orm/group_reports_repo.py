"""
群組檢舉（後台看的部分，c066）。檢舉本身在 group_messages_repo.report_message（快照在檢舉當下撈）。

跟私訊共用列表／處理的邏輯（dm_reports_repo.list_snapshot_reports／resolve_snapshot_report）；
差別是群組快照裡會有好幾個人講話，names 要涵蓋快照裡所有發言人，另外附群名與解散時間
（c068：群主解散後訊息與檢舉都留著，後台照樣看得到）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .dm_reports_repo import list_snapshot_reports, resolve_snapshot_report
from .models import GroupChat, GroupReport
from .session import using_session


def _iso(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


def _report_dict(r: GroupReport, names: Optional[dict] = None) -> dict:
    names = names or {}
    return {
        "id": r.id,
        "reporter_user_id": r.reporter_user_id,
        "reporter_username": names.get(r.reporter_user_id),
        "reported_user_id": r.reported_user_id,
        "reported_username": names.get(r.reported_user_id),
        "group_id": r.group_id,
        "message_id": r.message_id,
        "reason": r.reason,
        "note": r.note,
        "snapshot": r.snapshot,
        "names": names,  # 快照裡每個發言人（群組不只兩個人）
        "status": r.status,
        "admin_note": r.admin_note,
        "risk_score": r.risk_score,
        "risk_category": r.risk_category,
        "resolved_by": r.resolved_by,
        "resolved_at": _iso(r.resolved_at),
        "created_at": _iso(r.created_at),
    }


def _snapshot_speakers(r: GroupReport):
    return [m.get("from_user_id") for m in (r.snapshot or []) if m.get("from_user_id")]


class GroupReportsRepository:
    async def list_reports(
        self,
        status: str = "pending",
        page: int = 1,
        limit: int = 20,
        session: AsyncSession | None = None,
    ) -> dict:
        async with using_session(session) as s:
            result = await list_snapshot_reports(
                GroupReport,
                _report_dict,
                status,
                page,
                limit,
                extra_user_ids=_snapshot_speakers,
                session=s,
            )
            ids = {r["group_id"] for r in result["reports"]}
            groups = {}
            if ids:
                rows = await s.execute(
                    select(GroupChat.id, GroupChat.name, GroupChat.dissolved_at).where(
                        GroupChat.id.in_(ids)
                    )
                )
                groups = {gid: (name, dissolved) for gid, name, dissolved in rows}
        for r in result["reports"]:
            name, dissolved = groups.get(r["group_id"], (None, None))
            r["group_name"] = name
            r["group_dissolved_at"] = _iso(dissolved)  # 沒解散是 None
        return result

    async def resolve_report(
        self,
        report_id: int,
        status: str,
        admin_note: Optional[str],
        admin_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        return await resolve_snapshot_report(
            GroupReport,
            _report_dict,
            report_id,
            status,
            admin_note,
            admin_user_id,
            session=session,
        )


group_reports_repo = GroupReportsRepository()
