"""Agent Configs API（Model Mixer：per-agent 模型指定＋工具勾選）。

設計：docs/plans/2026-09-02-model-mixer-step1-schema.md（Step 1，方案 B）、
docs/plans/2026-09-03-model-mixer-step2-tools.md（Step 2）
上游：docs/plans/2026-09-02-model-mixer-design.md §2／§7／§8

治理邊界（與 agent_presets.py 同一套路）：
- 所有 endpoint 需登入（get_current_user）。
- 變更類 endpoint 為 Premium-only（per-agent 設定是 Mixer 功能的一部分）。
- agent_id 以 server 端 ProfileCatalog 驗證；model 以 MODEL_CONFIG 驗證
  （固定清單 provider 嚴格白名單；自由輸入 provider 接受非空字串）；
  tools 以 tools_catalog（_TOOLS_SEED）＋ CAPABILITY_TOOL_MAP 驗證。
- AGENT_PRESETS_ENABLED=off 時全部 404（與 presets 同一 feature flag，
  同一條回滾路徑）。
- 變更成功後失效 manager cache（快取的 manager 帶著舊 LLM client）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.agents.bootstrap import invalidate_manager_cache
from core.agents.capability_resolver import build_category_lookup
from core.agents.profile_catalog import (
    CAPABILITY_TOOL_MAP,
    get_profile_catalog,
)
from core.database.tools import normalize_membership_tier
from core.feature_flags import agent_presets_enabled
from core.model_config import (
    get_all_providers,
    is_free_input_provider,
    is_valid_model,
)
from core.orm.agent_configs_repo import agent_configs_repo
from core.orm.session import get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-configs"])

# 自由輸入 provider（openrouter/nvidia 等）的模型字串上限——防濫存長字串
_MAX_FREE_INPUT_MODEL_LEN = 200
# tools 白名單上限（已知工具 88 native ＋ 10 MCP = 98；上限只防濫用。
# 前端「取消一個就送全部」的模式會逼近這個值——工具總數再長就要一起調）
_MAX_TOOLS = 100
# 單一工具名長度上限——合法工具名遠短於此；純粹避免超長字串進 400 回顯
_MAX_TOOL_NAME_LEN = 100


def _require_presets_enabled() -> None:
    if not agent_presets_enabled():
        raise HTTPException(status_code=404, detail="Agent configs are not enabled")


def _require_premium(current_user: dict) -> None:
    tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    if tier != "premium":
        raise HTTPException(
            status_code=403,
            detail="Only Premium members can manage per-agent configs",
        )


def _config_to_dict(config) -> dict:
    return {
        "agent_id": config.agent_id,
        "model_selection": dict(config.model_selection)
        if isinstance(config.model_selection, dict)
        else None,
        "tools": list(config.tools) if isinstance(config.tools, list) else [],
        "skills": list(config.skills) if isinstance(config.skills, list) else [],
        "system_prompt": config.system_prompt or None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _validate_model_selection(selection: Dict[str, Any]) -> None:
    provider = str(selection.get("provider") or "").strip()
    model = str(selection.get("model") or "").strip()
    if provider not in get_all_providers():
        raise HTTPException(
            status_code=400, detail=f"Unsupported LLM provider: {provider}"
        )
    if not model:
        raise HTTPException(status_code=400, detail="model must not be empty")
    if len(model) > _MAX_FREE_INPUT_MODEL_LEN:
        raise HTTPException(status_code=400, detail="model too long")
    # 固定清單 provider 嚴格白名單；自由輸入 provider（前端是文字框）放行
    if not is_free_input_provider(provider) and not is_valid_model(provider, model):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model for provider {provider}: {model}",
        )


def _known_tool_names() -> set:
    """合法工具名：native（tools_catalog/_TOOLS_SEED）＋ MCP（capability map）。"""
    known = set(build_category_lookup())
    for names in CAPABILITY_TOOL_MAP.values():
        known.update(names)
    return known


def _known_skill_names(user_id: str) -> set:
    """合法 skill 名（Mixer Step 4）：官方（SkillLoader，全使用者相同）
    ＋本人自訂（pref store）。自訂 skill 是 per-user 資產——不能拿別人的
    名字設定。任何失敗回空集合（寫入端會因 unknown 全名被拒，fail-closed）。
    """
    known: set = set()
    try:
        from core.agents.skill_loader import get_skill_loader

        known.update(s.name for s in get_skill_loader().list_all())
        if user_id and user_id != "default":
            from core.database.skill_preferences import SkillPreferenceStore

            known.update(
                c.get("skill_name")
                for c in SkillPreferenceStore(user_id=user_id).get_custom_skills()
                if c.get("skill_name")
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("[agent-configs] known skill names load failed: %s", e)
        return set()
    return known


# skill 白名單上限（官方 ~15 ＋自訂；上限只防濫存）
_MAX_SKILLS = 64
# 單一 skill 名長度上限（合法名遠短於此；避免超長字串進 400 回顯）
_MAX_SKILL_NAME_LEN = 100


def _normalize_tools(raw: Optional[List[str]]) -> List[str]:
    """strip → 驗證 → 去重 → 排序；無效成員整筆拒絕，避免誤重設預設池。"""
    if raw is None:
        return []
    if any(not name.strip() or len(name) > _MAX_TOOL_NAME_LEN for name in raw):
        # 不回顯無效字串；也絕不把非空的錯誤輸入靜默降成 []（官方預設）。
        raise HTTPException(status_code=400, detail="Tool names must contain 1-100 characters")
    cleaned = sorted({name.strip() for name in raw})
    if len(cleaned) > _MAX_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Too many tools selected (max {_MAX_TOOLS})"
        )
    unknown = set(cleaned) - _known_tool_names()
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool names: {sorted(unknown)}",
        )
    return cleaned


def _normalize_skills(raw: Optional[List[str]], user_id: str) -> List[str]:
    """skill 版的 _normalize_tools：strip → 驗證 → 去重 → 排序；
    無效成員整筆拒絕，避免誤重設成 []（官方預設全上）。"""
    if raw is None:
        return []
    if any(not name.strip() or len(name) > _MAX_SKILL_NAME_LEN for name in raw):
        raise HTTPException(
            status_code=400, detail="Skill names must contain 1-100 characters"
        )
    cleaned = sorted({name.strip() for name in raw})
    if len(cleaned) > _MAX_SKILLS:
        raise HTTPException(
            status_code=400, detail=f"Too many skills selected (max {_MAX_SKILLS})"
        )
    unknown = set(cleaned) - _known_skill_names(user_id)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown skill names: {sorted(unknown)}",
        )
    return cleaned


async def load_agent_tool_selections(
    session: AsyncSession, current_user: dict, preset_id: str
) -> Optional[Dict[str, List[str]]]:
    """執行期／預覽共用的 per-agent 工具勾選載入器（Mixer Step 2）。

    - **preset 範圍**（c044）：設定屬於哪個 preset 就只在那個 preset 生效，
      沒有使用者層 fallback。呼叫端必須明確給 preset_id。
    - **非 Premium → None**：寫入端 Premium-only，讀取端必須重驗——使用者
      降級後殘留在 user_agent_configs 的勾選不得繼續生效（同
      _resolve_preset_model_override 的理由）。
    - 只收**非空**白名單：``tools=[]``＝未設定（官方預設），不過濾。
    - 任何失敗 → None（降級為官方預設，不阻斷聊天／預覽）。
    """
    try:
        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        if tier != "premium":
            return None
        if not preset_id:
            return None
        configs = await agent_configs_repo.list_configs(session, preset_id)
        selections: Dict[str, List[str]] = {}
        for config in configs:
            tools = config.tools if isinstance(config.tools, list) else []
            if tools:
                selections[config.agent_id] = [str(t) for t in tools]
        return selections or None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("[agent-configs] load tool selections failed: %s", e)
        return None


async def load_agent_skill_selections(
    session: AsyncSession, current_user: dict, preset_id: str
) -> Optional[Dict[str, List[str]]]:
    """執行期／預覽共用的 per-agent skill 勾選載入器（Mixer Step 4）。

    語意與 load_agent_tool_selections 完全對稱：preset 範圍、Premium 讀取端
    重驗（降級殘留不生效）、只收非空白名單（``skills=[]``＝未設定＝官方
    預設全上）、任何失敗 → None（降級為現行行為，不阻斷聊天）。
    """
    try:
        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        if tier != "premium":
            return None
        if not preset_id:
            return None
        configs = await agent_configs_repo.list_configs(session, preset_id)
        selections: Dict[str, List[str]] = {}
        for config in configs:
            skills = config.skills if isinstance(config.skills, list) else []
            if skills:
                selections[config.agent_id] = [str(s) for s in skills]
        return selections or None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("[agent-configs] load skill selections failed: %s", e)
        return None


async def load_agent_prompt_selections(
    session: AsyncSession, current_user: dict, preset_id: str
) -> Optional[Dict[str, str]]:
    """Per-agent 自訂 system prompt 載入器（2026-09-09 設計）。

    語意與 tool/skill 載入器對稱：preset 範圍、Premium 讀取端重驗（降級
    殘留不生效）、只收非空字串、任何失敗 → None（降級為無 per-agent
    prompt，不阻斷聊天）。
    """
    try:
        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        if tier != "premium":
            return None
        if not preset_id:
            return None
        configs = await agent_configs_repo.list_configs(session, preset_id)
        selections: Dict[str, str] = {}
        for config in configs:
            prompt = config.system_prompt if config.system_prompt else ""
            if prompt.strip():
                selections[config.agent_id] = prompt
        return selections or None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("[agent-configs] load prompt selections failed: %s", e)
        return None


class ModelSelectionInput(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=200)


#: Router 特例鍵（2026-09-09 設計）：Router 不是 ProfileCatalog 的 agent
#: profile（是分流分類器），但允許為它存 model_selection——UI 顯示實際
#: 生效模型並可指派。tools/skills/system_prompt 對 Router 無意義，一律拒。
_ROUTER_AGENT_ID = "router"

#: 與全域自訂 prompt 同一上限（user.py 的 analysis-preferences）
_MAX_SYSTEM_PROMPT_LEN = 2000


class AgentConfigPutInput(BaseModel):
    # null = 重設為官方預設；absent = 不更動（見 set_agent_config）
    model_selection: Optional[ModelSelectionInput] = None
    # Mixer Step 2：null / [] = 重設為未設定（官方預設工具池）
    tools: Optional[List[str]] = Field(default=None, max_length=_MAX_TOOLS)
    # Mixer Step 4：null / [] = 重設為未設定（官方預設 skill 全上）
    skills: Optional[List[str]] = Field(default=None, max_length=_MAX_SKILLS)
    # 2026-09-09：null = 重設為未設定；absent = 不更動
    system_prompt: Optional[str] = Field(default=None, max_length=_MAX_SYSTEM_PROMPT_LEN)


async def _owned_preset(session: AsyncSession, current_user: dict, preset_id: str):
    """驗證 preset 屬於本人，並回傳它。

    c044 起 per-agent 設定的作用域是 preset，路由因此改成巢狀——把 preset_id
    放在路徑上，呼叫端**不可能忘記帶**。放成選填 query param 的話，漏帶就會
    悄悄操作到別的範圍，正是這次要根治的那類問題。
    """
    from core.orm.agent_presets_repo import agent_presets_repo

    preset = await agent_presets_repo.get_preset(
        session, current_user.get("user_id"), preset_id
    )
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset


@router.get("/api/agent-presets/{preset_id}/agent-configs")
@limiter.limit("30/minute")
async def list_agent_configs(
    request: Request,
    preset_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """列出這個 preset 的 per-agent 設定（讀取不限 Premium）。"""
    _require_presets_enabled()
    await _owned_preset(session, current_user, preset_id)
    configs = await agent_configs_repo.list_configs(session, preset_id)
    return {
        "success": True,
        "configs": [_config_to_dict(c) for c in configs],
    }


@router.put("/api/agent-presets/{preset_id}/agent-configs/{agent_id}")
@limiter.limit("20/minute")
async def set_agent_config(
    request: Request,
    preset_id: str,
    agent_id: str,
    payload: AgentConfigPutInput,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Upsert per-agent 設定。

    欄位語義：``model_selection`` / ``tools`` / ``skills`` 各自獨立——
    **absent = 不更動**（混合版本與單欄更新的呼叫端安全）、
    **null = 重設為官方預設**。absent 欄位不傳入 repo，首寫重試也只更新
    本次指定欄位。
    """
    _require_presets_enabled()
    _require_premium(current_user)
    await _owned_preset(session, current_user, preset_id)

    provided = payload.model_fields_set

    # Router 特例（2026-09-09 設計）：不是 ProfileCatalog agent，只收
    # model_selection——分流器沒有工具池/skill/prompt 的概念。
    if agent_id == _ROUTER_AGENT_ID:
        if provided - {"model_selection"}:
            raise HTTPException(
                status_code=400,
                detail="Router config only accepts model_selection",
            )
    else:
        catalog = get_profile_catalog()
        valid, unknown = catalog.validate_agent_ids([agent_id])
        if unknown or not valid:
            raise HTTPException(status_code=400, detail=f"Unknown agent profile: {agent_id}")

    if (
        "model_selection" not in provided
        and "tools" not in provided
        and "skills" not in provided
        and "system_prompt" not in provided
    ):
        # 全欄 absent：沒有要更新的東西——明確 400，避免誤寫成「整列重設」
        raise HTTPException(
            status_code=400,
            detail="Provide model_selection, tools, skills or system_prompt to update",
        )

    updates: Dict[str, Any] = {}
    if "model_selection" in provided:
        selection = None
        if payload.model_selection is not None:
            selection = {
                "provider": payload.model_selection.provider.strip(),
                "model": payload.model_selection.model.strip(),
            }
            _validate_model_selection(selection)
        updates["model_selection"] = selection

    if "tools" in provided:
        updates["tools"] = _normalize_tools(payload.tools)

    if "skills" in provided:
        updates["skills"] = _normalize_skills(
            payload.skills, current_user.get("user_id", "")
        )

    if "system_prompt" in provided:
        # 與全域自訂 prompt 同一道防護（jailbreak 過濾）；null/空字串＝重設
        prompt = payload.system_prompt
        if prompt is not None:
            from core.agents.prompt_guard import sanitize_system_prompt

            prompt = sanitize_system_prompt(prompt.strip())
            if prompt is not None and not prompt.strip():
                prompt = None
        updates["system_prompt"] = prompt

    user_id = current_user.get("user_id")
    config = await agent_configs_repo.set_config(
        session, preset_id, agent_id, user_id, **updates
    )
    invalidate_manager_cache(user_id)
    return {"success": True, "config": _config_to_dict(config)}


@router.delete("/api/agent-presets/{preset_id}/agent-configs/{agent_id}")
@limiter.limit("20/minute")
async def delete_agent_config(
    request: Request,
    preset_id: str,
    agent_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    _require_presets_enabled()
    _require_premium(current_user)
    await _owned_preset(session, current_user, preset_id)
    user_id = current_user.get("user_id")
    deleted = await agent_configs_repo.delete_config(session, preset_id, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent config not found")
    invalidate_manager_cache(user_id)
    return {"success": True}
