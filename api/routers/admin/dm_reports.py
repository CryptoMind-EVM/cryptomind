"""
Admin 私訊檢舉（2026-09-29，c061）

只有管理員看得到私訊檢舉的快照——不走論壇的社群投票（私訊不能給其他會員看）。
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import require_admin
from api.middleware.rate_limit import limiter
from core.orm.config_repo import _write_audit_log
from core.orm.dm_reports_repo import dm_reports_repo
from core.orm.session import get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - DM Reports"])


class ResolveDmReportRequest(BaseModel):
    status: Literal["resolved", "dismissed"]
    admin_note: Optional[str] = Field(None, max_length=1000)


@router.get("/dm-reports")
@limiter.limit("30/minute")
async def admin_list_dm_reports(
    request: Request,
    status: str = Query("pending", pattern="^(pending|resolved|dismissed)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin_user: dict = Depends(require_admin),
):
    """列出私訊檢舉（含快照）"""
    return await dm_reports_repo.list_reports(status=status, page=page, limit=limit)


@router.post("/dm-reports/{report_id}/resolve")
@limiter.limit("30/minute")
async def admin_resolve_dm_report(
    report_id: int,
    request: Request,
    body: ResolveDmReportRequest,
    admin_user: dict = Depends(require_admin),
):
    """標記已處理／不成立，寫進稽核紀錄"""
    factory = get_session_factory()
    async with factory() as session:
        result = await dm_reports_repo.resolve_report(
            report_id,
            body.status,
            body.admin_note,
            admin_user["user_id"],
            session=session,
        )
        if not result["success"]:
            error = result.get("error")
            if error == "report_not_found":
                raise HTTPException(status_code=404, detail="Report not found")
            if error == "already_resolved":
                raise HTTPException(status_code=409, detail="Report already resolved")
            raise HTTPException(status_code=400, detail=error)
        await _write_audit_log(
            session,
            f"admin_dm_report:resolve:{report_id}",
            "pending",
            body.status,
            admin_user["user_id"],
        )
        await session.commit()
    return {"success": True, "report": result["report"]}
