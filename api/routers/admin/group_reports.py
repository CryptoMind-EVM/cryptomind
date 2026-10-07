"""
Admin 群組檢舉（c066）

跟私訊檢舉一樣只有管理員看得到快照；快照是檢舉當下被檢舉那則＋前 10 則（限檢舉者看得到的範圍）。
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import require_admin
from api.middleware.rate_limit import limiter
from core.orm.config_repo import _write_audit_log
from core.orm.group_reports_repo import group_reports_repo
from core.orm.session import get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Group Reports"])


class ResolveGroupReportRequest(BaseModel):
    status: Literal["resolved", "dismissed"]
    admin_note: Optional[str] = Field(None, max_length=1000)


@router.get("/group-reports")
@limiter.limit("30/minute")
async def admin_list_group_reports(
    request: Request,
    status: str = Query("pending", pattern="^(pending|resolved|dismissed)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin_user: dict = Depends(require_admin),
):
    """列出群組檢舉（含快照、群名、快照裡每個發言人的名字）"""
    return await group_reports_repo.list_reports(status=status, page=page, limit=limit)


@router.post("/group-reports/{report_id}/resolve")
@limiter.limit("30/minute")
async def admin_resolve_group_report(
    report_id: int,
    request: Request,
    body: ResolveGroupReportRequest,
    admin_user: dict = Depends(require_admin),
):
    """標記已處理／不成立，寫進稽核紀錄"""
    factory = get_session_factory()
    async with factory() as session:
        result = await group_reports_repo.resolve_report(
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
            f"admin_group_report:resolve:{report_id}",
            "pending",
            body.status,
            admin_user["user_id"],
        )
        await session.commit()
    return {"success": True, "report": result["report"]}
