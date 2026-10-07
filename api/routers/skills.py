"""Skill Management API Router — 使用者管理官方 skill 開關 + 自訂 skill。

Endpoints:
  GET    /api/skills                  — 列出官方 skill + 使用者開關狀態 + 自訂 skill
  POST   /api/skills/{name}/toggle    — 開關官方 skill
  GET    /api/skills/custom           — 列出自訂 skill
  POST   /api/skills/custom           — 新建自訂 skill
  PATCH  /api/skills/custom/{name}    — 修改自訂 skill
  DELETE /api/skills/custom/{name}    — 刪除自訂 skill

安全：
- 官方 skill 不可修改內容，只可開關
- 自訂 skill body ≤3000 字、數量上限 10 個（SkillPreferenceStore 驗證）
- API 不回傳安全框架（<user_skill> 標籤只在後端注入時組裝）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)


class RollbackRequest(BaseModel):
    target_revision: int = Field(ge=1, le=1000)

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────


class ToggleSkillRequest(BaseModel):
    is_enabled: bool = Field(..., description="啟用或關閉")


class CreateCustomSkillRequest(BaseModel):
    skill_name: str = Field(..., description="skill 名稱（英數/底線/連字號）", max_length=50)
    description: str = Field(default="", description="一句話描述", max_length=200)
    trigger_keywords: str = Field(default="", description="觸發詞（逗號分隔）", max_length=200)
    body: str = Field(..., description="skill 內容（分析方法指引）", max_length=3000)


class UpdateCustomSkillRequest(BaseModel):
    description: str | None = Field(default=None, max_length=200)
    trigger_keywords: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=3000)
    is_enabled: bool | None = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _get_pref_store(user_id: str):
    from core.database.skill_preferences import SkillPreferenceStore

    return SkillPreferenceStore(user_id=user_id)


def _get_official_skills():
    """從 SkillLoader 取官方 skill 的輕量資訊（不含完整 body，避免回傳過大）。"""
    from core.agents.skill_loader import get_skill_loader

    loader = get_skill_loader()
    return [
        {
            "name": s.name,
            "description": s.description,
            "eager_load": s.eager_load,
            "auto_fire_keywords": s.auto_fire_keywords[:5],  # 只回前5個做預覽
            "is_official": True,
        }
        for s in loader.list_all()
    ]


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/api/skills")
@limiter.limit("30/minute")
async def list_skills(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """列出官方 skill（含使用者開關狀態）+ 自訂 skill。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        disabled = store.get_disabled_skills()
        official = _get_official_skills()
        for s in official:
            s["is_enabled"] = s["name"] not in disabled

        custom = store.get_custom_skills()
        custom_out = [
            {
                "name": c["skill_name"],
                "description": c.get("description", ""),
                "trigger_keywords": c.get("trigger_keywords", ""),
                "body": c.get("body", ""),
                "is_enabled": c.get("is_enabled", True),
                "is_official": False,
            }
            for c in custom
        ]
        return {
            "official": official,
            "custom": custom_out,
            "total": len(official) + len(custom_out),
        }
    except Exception as exc:
        logger.warning(f"[skills] list failed for {user_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to load the skill list") from exc


@router.post("/api/skills/{skill_name}/toggle")
@limiter.limit("20/minute")
async def toggle_official_skill(
    request: Request,
    skill_name: str,
    body: ToggleSkillRequest,
    current_user: dict = Depends(get_current_user),
):
    """開關官方 skill。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        ok = store.set_skill_enabled(skill_name, body.is_enabled)
        if not ok:
            raise HTTPException(status_code=500, detail="Update failed")
        return {"ok": True, "skill_name": skill_name, "is_enabled": body.is_enabled}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[skills] toggle failed for {user_id}: {exc}")
        raise HTTPException(status_code=500, detail="Update failed") from exc


@router.post("/api/skills/custom")
@limiter.limit("10/minute")
async def create_custom_skill(
    request: Request,
    body: CreateCustomSkillRequest,
    current_user: dict = Depends(get_current_user),
):
    """新建自訂 skill。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        result = store.create_custom_skill(
            skill_name=body.skill_name,
            description=body.description,
            trigger_keywords=body.trigger_keywords,
            body=body.body,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Create failed"))
        return {"ok": True, "skill_name": body.skill_name}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[skills] create custom failed for {user_id}: {exc}")
        raise HTTPException(status_code=500, detail="Create failed") from exc


@router.patch("/api/skills/custom/{skill_name}")
@limiter.limit("20/minute")
async def update_custom_skill(
    request: Request,
    skill_name: str,
    body: UpdateCustomSkillRequest,
    current_user: dict = Depends(get_current_user),
):
    """修改自訂 skill。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        result = store.update_custom_skill(
            skill_name=skill_name,
            description=body.description,
            trigger_keywords=body.trigger_keywords,
            body=body.body,
            is_enabled=body.is_enabled,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Update failed"))
        return {"ok": True, "skill_name": skill_name}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[skills] update custom failed for {user_id}: {exc}")
        raise HTTPException(status_code=500, detail="Update failed") from exc


@router.delete("/api/skills/custom/{skill_name}")
@limiter.limit("20/minute")
async def delete_custom_skill(
    request: Request,
    skill_name: str,
    current_user: dict = Depends(get_current_user),
):
    """刪除自訂 skill。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        result = store.delete_custom_skill(skill_name)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Delete failed"))
        return {"ok": True, "skill_name": skill_name}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[skills] delete custom failed for {user_id}: {exc}")
        raise HTTPException(status_code=500, detail="Delete failed") from exc


# ── Skill 版本歷史（治理 Part B：c031 的 user_skill_revisions 表已存在）──


@router.get("/api/skills/custom/{skill_name}/history")
@limiter.limit("20/minute")
async def get_skill_history(
    request: Request,
    skill_name: str,
    current_user: dict = Depends(get_current_user),
):
    """列出自訂 skill 的版本歷史。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        revisions = store.get_skill_revisions(skill_name)
        return {"history": revisions, "count": len(revisions)}
    except Exception as exc:
        logger.warning(f"[skills] history failed for {user_id}/{skill_name}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get history") from exc


@router.post("/api/skills/custom/{skill_name}/rollback")
@limiter.limit("10/minute")
async def rollback_skill(
    request: Request,
    skill_name: str,
    body: RollbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """回滾自訂 skill 到指定版本。"""
    user_id = current_user["user_id"]
    try:
        store = _get_pref_store(user_id)
        result = store.rollback_skill(skill_name, body.target_revision)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Rollback failed"))
        return {"ok": True, "skill_name": skill_name, "revision": body.target_revision}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[skills] rollback failed for {user_id}/{skill_name}: {exc}")
        raise HTTPException(status_code=500, detail="Rollback failed") from exc

