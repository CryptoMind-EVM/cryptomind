"""Agent Presets API（impl plan Task B4；design §13）。

治理邊界：
- 所有 endpoint 需登入（get_current_user）。
- 變更類 endpoint 為 Premium-only（design §16：自訂 Preset／Team 是 Premium 功能）。
- agent_ids／capability 鍵一律以 server 端 ProfileCatalog 驗證（不信任 client）。
- AGENT_PRESETS_ENABLED=off 時全部 404（feature flag 回滾路徑）。
- 變更成功後失效 manager cache（§13 安全要求）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.routers.agent_configs import (
    load_agent_skill_selections,
    load_agent_tool_selections,
)
from core.agents.bootstrap import invalidate_manager_cache
from core.agents.capability_resolver import (
    build_profile_candidate_tools,
    resolve_preset_config,
)
from core.agents.profile_catalog import (
    CATALOG_VERSION,
    get_profile_catalog,
)
from core.database.tools import normalize_membership_tier
from core.feature_flags import agent_presets_enabled
from core.orm.agent_presets_repo import (
    MAX_PRESETS_PER_USER,
    agent_presets_repo,
)
from core.orm.session import get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-presets"])


def _require_presets_enabled() -> None:
    if not agent_presets_enabled():
        raise HTTPException(status_code=404, detail="Agent presets are not enabled")


def _require_premium(current_user: dict) -> None:
    tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    if tier != "premium":
        raise HTTPException(
            status_code=403,
            detail="Only Premium members can manage agent presets",
        )


def _preset_to_dict(preset) -> dict:
    return {
        "preset_id": preset.preset_id,
        "name": preset.name,
        "mode": preset.mode,
        "agent_ids": list(preset.agent_ids or []),
        "analysis_mode": preset.analysis_mode,
        "action_policy": preset.action_policy,
        "capability_overrides": dict(preset.capability_overrides or {}),
        "is_default": bool(preset.is_default),
        "config_version": preset.config_version,
        "created_at": preset.created_at.isoformat() if preset.created_at else None,
        "updated_at": preset.updated_at.isoformat() if preset.updated_at else None,
    }


class AgentPresetCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="顯示名稱（1–50 字）")
    mode: Literal["single", "auto", "team"] = "single"
    agent_ids: List[str] = Field(min_length=1, max_length=4)
    analysis_mode: Literal["quick", "verified", "research"] = "quick"
    action_policy: Literal["read_only", "confirm_actions"] = "read_only"
    capability_overrides: Dict[str, bool] = Field(default_factory=dict)
    is_default: bool = False


class AgentPresetUpdateInput(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    mode: Optional[Literal["single", "auto", "team"]] = None
    agent_ids: Optional[List[str]] = Field(default=None, min_length=1, max_length=4)
    analysis_mode: Optional[Literal["quick", "verified", "research"]] = None
    action_policy: Optional[Literal["read_only", "confirm_actions"]] = None
    capability_overrides: Optional[Dict[str, bool]] = None
    is_default: Optional[bool] = None


def _validate_against_catalog(agent_ids: List[str], overrides: Dict[str, bool]) -> None:
    catalog = get_profile_catalog()
    valid, unknown = catalog.validate_agent_ids(agent_ids)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown agent profiles: {unknown}",
        )
    known_caps = catalog.all_capabilities()
    unknown_caps = [c for c in overrides if c not in known_caps]
    if unknown_caps:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown capabilities: {unknown_caps}",
        )
    bad_values = {c: v for c, v in overrides.items() if not isinstance(v, bool)}
    if bad_values:
        raise HTTPException(
            status_code=400,
            detail=f"Capability overrides must be boolean: {sorted(bad_values)}",
        )


def _skills_catalog_for_user(user_id: str) -> List[dict]:
    """Mixer Step 4：skill 勾選 UI 的資料源（輕量，**不含 body**）。

    官方 skill 全列（SkillLoader；applies_to 只當 UI 提示——AI Studio 執行
    全走 cryptomind 的 match_all_skills 目錄，不做 per-profile 假精確映射）
    ＋本人啟用中的自訂 skill（名稱＋描述；body 是 Settings 編輯器的事）。
    任何失敗回空清單（UI 退化成沒得勾，不阻斷 AI Studio）。
    """
    try:
        from core.agents.skill_loader import get_skill_loader

        official = [
            {
                "name": s.name,
                "description": s.description,
                "applies_to": list(s.applies_to or []),
                "eager_load": bool(s.eager_load),
                "is_official": True,
            }
            for s in get_skill_loader().list_all()
        ]
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("[agent-profiles] official skills catalog load failed: %s", e)
        return []
    custom: List[dict] = []
    if user_id and user_id != "default":
        try:
            from core.database.skill_preferences import SkillPreferenceStore

            custom = [
                {
                    "name": c.get("skill_name", ""),
                    "description": c.get("description", ""),
                    "applies_to": [],
                    "eager_load": False,
                    "is_official": False,
                }
                for c in SkillPreferenceStore(
                    user_id=user_id
                ).get_enabled_custom_skills()
                if c.get("skill_name")
            ]
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning("[agent-profiles] custom skills catalog load failed: %s", e)
    return official + custom


@router.get("/api/agent-profiles")
@limiter.limit("30/minute")
async def list_agent_profiles(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """官方 Agent Profile catalog（唯讀）。

    Mixer Step 2：每個 profile 附 ``candidate_tools``（勾選 UI 資料源，
    native 依 allowed_tool_categories、MCP 依 capability map）＋ ``required``
    （必要工具，UI 需鎖定不可取消）＋ ``locked``（tier 不足，僅供顯示）。
    Mixer Step 4：回應附 ``skills_catalog``（per-agent skill 勾選 UI 的資料
    源——官方＋本人自訂的輕量清單，各 agent 卡共用同一份）。
    """
    _require_presets_enabled()
    catalog = get_profile_catalog()
    user_tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    profiles = []
    for p in catalog.list_profiles():
        payload = p.to_api_dict()
        required = set(p.required_tools)
        candidates = []
        for tool in build_profile_candidate_tools(p):
            tool["required"] = tool["tool_id"] in required
            tool["locked"] = (
                str(tool.get("required_tier", "free")).lower() == "premium"
                and user_tier != "premium"
            )
            candidates.append(tool)
        payload["candidate_tools"] = candidates
        profiles.append(payload)
    return {
        "success": True,
        "config_version": CATALOG_VERSION,
        "profiles": profiles,
        "skills_catalog": _skills_catalog_for_user(current_user.get("user_id", "")),
    }


@router.get("/api/agent-presets")
@limiter.limit("30/minute")
async def list_agent_presets(
    request: Request,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """列出使用者 presets；Free 使用者回官方預設設定（無自訂）。"""
    _require_presets_enabled()
    user_id = current_user.get("user_id")
    presets = await agent_presets_repo.list_presets(session, user_id)
    return {
        "success": True,
        "presets": [_preset_to_dict(p) for p in presets],
        "quota": {"max": MAX_PRESETS_PER_USER, "used": len(presets)},
        "official_default": {
            "agent_ids": ["general_research"],
            "analysis_mode": "quick",
            "action_policy": "read_only",
        },
    }


# ── 團隊模板 ─────────────────────────────────────────────────────────────
# preset 就是團隊；三個官方模板一鍵建立多 agent preset（工具池取聯集）。
PRESET_TEMPLATES: Dict[str, Dict[str, object]] = {
    "token_diligence": {
        "names": {
            "zh-TW": "代幣盡調：市場＋風控",
            "zh-CN": "代币尽调：市场＋风控",
            "en": "Token diligence: markets + risk",
            "ru": "Проверка токена: рынок + риск",
        },
        "descriptions": {
            "zh-TW": "先查合約安全再看行情與技術面",
            "zh-CN": "先查合约安全再看行情与技术面",
            "en": "Contract safety first, then price and technicals",
            "ru": "Сначала безопасность контракта, потом цена и техника",
        },
        "agent_ids": ["finance_markets", "onchain_security"],
        "mode": "team",
        "analysis_mode": "verified",
    },
    "tw_macro": {
        "names": {
            "zh-TW": "台股加總經",
            "zh-CN": "台股加宏观",
            "en": "TW stocks + macro",
            "ru": "Акции Тайваня + макро",
        },
        "descriptions": {
            "zh-TW": "個股看法人與技術面，總經環境一起考慮",
            "zh-CN": "个股看法人与技术面，宏观环境一起考虑",
            "en": "Stock view with institutional flows and technicals, macro in context",
            "ru": "Взгляд на акцию с потоками и техникой в макро-контексте",
        },
        "agent_ids": ["finance_markets", "general_research"],
        "mode": "team",
        "analysis_mode": "quick",
    },
    "research_markets": {
        "names": {
            "zh-TW": "研究員配市場",
            "zh-CN": "研究员配市场",
            "en": "Researcher + markets",
            "ru": "Исследователь + рынки",
        },
        "descriptions": {
            "zh-TW": "開放式問題：先查資料再對照行情",
            "zh-CN": "开放式问题：先查资料再对照行情",
            "en": "Open-ended questions: research first, then check the market",
            "ru": "Открытые вопросы: сначала исследование, потом рынок",
        },
        "agent_ids": ["general_research", "finance_markets"],
        "mode": "team",
        "analysis_mode": "research",
    },
}


def _template_view(template_id: str, spec: Dict[str, object], language: str) -> dict:
    names = spec["names"]  # type: ignore[index]
    descs = spec["descriptions"]  # type: ignore[index]
    return {
        "id": template_id,
        "name": names.get(language) or names["zh-TW"],  # type: ignore[union-attr]
        "description": descs.get(language) or descs["zh-TW"],  # type: ignore[union-attr]
        "agent_ids": list(spec["agent_ids"]),  # type: ignore[arg-type]
        "mode": spec["mode"],
        "analysis_mode": spec["analysis_mode"],
    }


@router.get("/api/agent-presets/templates")
@limiter.limit("30/minute")
async def list_preset_templates(
    request: Request,
    language: str = "zh-TW",
    current_user: dict = Depends(get_current_user),
):
    """官方團隊模板（任何登入使用者可看；建立仍需 Premium）。"""
    _require_presets_enabled()
    lang = language if language in ("zh-TW", "zh-CN", "en", "ru") else "zh-TW"
    return {
        "success": True,
        "templates": [
            _template_view(tid, spec, lang) for tid, spec in PRESET_TEMPLATES.items()
        ],
    }


async def _should_auto_default(session: AsyncSession, user_id: str) -> bool:
    """使用者還沒有作用中（is_default）的 preset 時，新建的那個直接設為作用中。

    2026-09-22 DANNY：從模板建了三個 preset、沒有一個是預設 → Agents 分頁的模型下拉全部停用
    （c044：per-agent 設定的作用域是作用中的 preset），使用者只看到「不能選模型」。第一個
    preset 本來就該是作用中的，不該多一個「設為預設」的步驟才能開始配置。
    """
    return await agent_presets_repo.get_default_preset(session, user_id) is None


@router.post("/api/agent-presets/from-template/{template_id}", status_code=201)
@limiter.limit("20/minute")
async def create_preset_from_template(
    request: Request,
    template_id: str,
    language: str = "zh-TW",
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """一鍵從模板建立 preset（與 create_agent_preset 同一條路徑與限制）。"""
    _require_presets_enabled()
    _require_premium(current_user)
    spec = PRESET_TEMPLATES.get(template_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Template not found")
    view = _template_view(
        template_id,
        spec,
        language if language in ("zh-TW", "zh-CN", "en", "ru") else "zh-TW",
    )
    _validate_against_catalog(view["agent_ids"], {})
    user_id = current_user.get("user_id")
    count = await agent_presets_repo.count_presets(session, user_id)
    if count >= MAX_PRESETS_PER_USER:
        raise HTTPException(status_code=409, detail="Preset limit reached")
    preset = await agent_presets_repo.create_preset(
        session,
        user_id=user_id,
        name=view["name"],
        mode=view["mode"],
        agent_ids=view["agent_ids"],
        analysis_mode=view["analysis_mode"],
        action_policy="read_only",
        capability_overrides={},
        is_default=await _should_auto_default(session, user_id),
        config_version=CATALOG_VERSION,
    )
    invalidate_manager_cache(user_id)
    return {"success": True, "preset": _preset_to_dict(preset), "template": view}


@router.post("/api/agent-presets", status_code=201)
@limiter.limit("20/minute")
async def create_agent_preset(
    request: Request,
    payload: AgentPresetCreateInput,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    _require_presets_enabled()
    _require_premium(current_user)
    _validate_against_catalog(payload.agent_ids, payload.capability_overrides)

    user_id = current_user.get("user_id")
    count = await agent_presets_repo.count_presets(session, user_id)
    if count >= MAX_PRESETS_PER_USER:
        raise HTTPException(status_code=409, detail="Preset limit reached")

    preset = await agent_presets_repo.create_preset(
        session,
        user_id=user_id,
        name=payload.name.strip(),
        mode=payload.mode,
        agent_ids=payload.agent_ids,
        analysis_mode=payload.analysis_mode,
        action_policy=payload.action_policy,
        capability_overrides=payload.capability_overrides,
        is_default=payload.is_default or await _should_auto_default(session, user_id),
        config_version=CATALOG_VERSION,
    )
    invalidate_manager_cache(user_id)
    return {"success": True, "preset": _preset_to_dict(preset)}


@router.patch("/api/agent-presets/{preset_id}")
@limiter.limit("20/minute")
async def update_agent_preset(
    request: Request,
    preset_id: str,
    payload: AgentPresetUpdateInput,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    _require_presets_enabled()
    _require_premium(current_user)
    user_id = current_user.get("user_id")
    preset = await agent_presets_repo.get_preset(session, user_id, preset_id)
    if preset is None:
        # 非 owner 與不存在一律 404（防枚舉）
        raise HTTPException(status_code=404, detail="Preset not found")

    next_agent_ids = payload.agent_ids or list(preset.agent_ids or [])
    next_overrides = (
        payload.capability_overrides
        if payload.capability_overrides is not None
        else dict(preset.capability_overrides or {})
    )
    _validate_against_catalog(next_agent_ids, next_overrides)

    preset = await agent_presets_repo.update_preset(
        session,
        preset,
        name=payload.name,
        mode=payload.mode,
        agent_ids=payload.agent_ids,
        analysis_mode=payload.analysis_mode,
        action_policy=payload.action_policy,
        capability_overrides=payload.capability_overrides,
    )
    if payload.is_default is True:
        await agent_presets_repo.set_default(session, user_id, preset_id)
        await session.refresh(preset)
    invalidate_manager_cache(user_id)
    return {"success": True, "preset": _preset_to_dict(preset)}


@router.delete("/api/agent-presets/{preset_id}")
@limiter.limit("20/minute")
async def delete_agent_preset(
    request: Request,
    preset_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    _require_presets_enabled()
    _require_premium(current_user)
    user_id = current_user.get("user_id")
    deleted = await agent_presets_repo.delete_preset(session, user_id, preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preset not found")
    invalidate_manager_cache(user_id)
    return {"success": True}


@router.post("/api/agent-presets/{preset_id}/activate")
@limiter.limit("20/minute")
async def activate_agent_preset(
    request: Request,
    preset_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    _require_presets_enabled()
    user_id = current_user.get("user_id")
    ok = await agent_presets_repo.set_default(session, user_id, preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Preset not found")
    preset = await agent_presets_repo.get_preset(session, user_id, preset_id)
    invalidate_manager_cache(user_id)
    tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    summary = resolve_preset_config(
        {
            "agent_ids": list(preset.agent_ids or []),
            "mode": preset.mode,
            "analysis_mode": preset.analysis_mode,
            "action_policy": preset.action_policy,
            "capability_overrides": dict(preset.capability_overrides or {}),
        },
        user_tier=tier,
        agent_tool_selections=await load_agent_tool_selections(
            session, current_user, preset.preset_id
        ),
    )
    return {
        "success": True,
        "preset_id": preset_id,
        "summary": {
            "tool_count": len(summary["tool_names"]),
            "profiles": summary["profiles"],
            "action_policy": summary["action_policy"],
            "config_hash": summary["config_hash"],
        },
    }


@router.get("/api/agent-presets/{preset_id}/resolved-capabilities")
@limiter.limit("30/minute")
async def get_resolved_capabilities(
    request: Request,
    preset_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    _require_presets_enabled()
    user_id = current_user.get("user_id")
    preset = await agent_presets_repo.get_preset(session, user_id, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    resolved = resolve_preset_config(
        {
            "agent_ids": list(preset.agent_ids or []),
            "mode": preset.mode,
            "analysis_mode": preset.analysis_mode,
            "action_policy": preset.action_policy,
            "capability_overrides": dict(preset.capability_overrides or {}),
        },
        user_tier=tier,
        # 預覽與執行期同一套勾選（analysis.py 同一 helper）——避免預覽說謊
        agent_tool_selections=await load_agent_tool_selections(
            session, current_user, preset.preset_id
        ),
    )
    return {
        "success": True,
        "resolved": resolved,
        "agent_skill_selections": (
            await load_agent_skill_selections(session, current_user, preset.preset_id)
        )
        or {},
    }
