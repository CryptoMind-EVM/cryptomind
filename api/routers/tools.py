"""
Tools API Router

Endpoints for listing tools and managing user tool preferences.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core.database.tools import get_tools_catalog_fallback, seed_tools_catalog
from core.orm.tools_repo import _normalize_tier as normalize_membership_tier
from core.orm.tools_repo import tools_repo

router = APIRouter()

# 60s TTL cache for /api/tools and /api/user/tools to avoid 3 sequential
# DB roundtrips on every render. Neon SG latency is ~200-300ms/hop, so
# the hot path goes from ~1s to ~5ms on cache HIT.
_TOOLS_CACHE_TTL_SECONDS: float = 60.0
_TOOLS_CACHE_MAX_ENTRIES: int = 2000
_tools_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_tools_cache_lock = asyncio.Lock()


def invalidate_tools_cache(
    user_tier: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Invalidate the tools list cache.

    Args:
        user_tier: If set, only clear entries matching this tier (case-insensitive).
        user_id:   If set, only clear entries matching this user.
        If both are None, clear the entire cache.
    """
    has_tier = user_tier is not None
    has_user = user_id is not None
    target_tier = user_tier.lower() if has_tier else None
    target_user = user_id or ""
    if not has_tier and not has_user:
        _tools_cache.clear()
        return
    keys = [
        k
        for k in list(_tools_cache.keys())
        if (not has_tier or k[0] == target_tier)
        and (not has_user or k[1] == target_user)
    ]
    for k in keys:
        _tools_cache.pop(k, None)


async def _fetch_tools_with_fallback(
    user_tier: str,
    user_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Pure data-fetching logic: DB → seed → static fallback chain.

    Returns an empty list if all sources fail (caller decides whether to cache).
    """
    try:
        tools = await tools_repo.get_tools_for_frontend(user_tier, user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[tools] primary load failed, falling back: {e}")
        tools = []

    if not tools:
        try:
            await run_sync(seed_tools_catalog)
            tools = await tools_repo.get_tools_for_frontend(user_tier, user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[tools] auto-seed failed, using static fallback: {e}")
            tools = get_tools_catalog_fallback(user_tier)
    return tools


async def _list_tools_impl(current_user: dict) -> dict:
    """Return all tools with per-user enabled/locked status (60s TTL cache)."""
    user_tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    user_id = current_user.get("user_id") or ""
    cache_key = (user_tier, user_id)

    now = time.monotonic()
    async with _tools_cache_lock:
        hit = _tools_cache.get(cache_key)
        if hit is not None and now - hit[0] < _TOOLS_CACHE_TTL_SECONDS:
            logger.debug(f"[tools] cache HIT key={cache_key}")
            return hit[1]

    tools = await _fetch_tools_with_fallback(user_tier, user_id)
    response = {"tools": tools, "user_tier": user_tier}

    # Only cache non-empty results — avoid locking in fallback data if the DB
    # recovers within the TTL window.
    if tools:
        stored_at = time.monotonic()
        async with _tools_cache_lock:
            # 順手剔除已過期項，避免不活躍用戶的舊 entry 永久累積
            expired = [
                k
                for k, (ts, _) in _tools_cache.items()
                if stored_at - ts >= _TOOLS_CACHE_TTL_SECONDS
            ]
            for k in expired:
                _tools_cache.pop(k, None)
            _tools_cache[cache_key] = (stored_at, response)
            # 硬上限保護：仍超量時，淘汰最舊的項
            if len(_tools_cache) > _TOOLS_CACHE_MAX_ENTRIES:
                overflow = len(_tools_cache) - _TOOLS_CACHE_MAX_ENTRIES
                for k in sorted(_tools_cache, key=lambda key: _tools_cache[key][0])[
                    :overflow
                ]:
                    _tools_cache.pop(k, None)

    return response


@router.get("/api/tools")
async def list_tools(current_user: dict = Depends(get_current_user)):
    return await _list_tools_impl(current_user)


@router.get("/api/user/tools")
async def list_user_tools(current_user: dict = Depends(get_current_user)):
    return await _list_tools_impl(current_user)


class ToolPreferenceRequest(BaseModel):
    is_enabled: bool


async def _set_tool_preference_impl(
    tool_id: str,
    request: ToolPreferenceRequest,
    current_user: dict,
) -> dict:
    """Toggle a tool on/off (premium tier only)."""
    user_tier = normalize_membership_tier(current_user.get("membership_tier", "free"))

    if user_tier != "premium":
        raise HTTPException(status_code=403, detail="Only Premium members can customize tool preferences")

    user_id = current_user.get("user_id")
    tools = await tools_repo.get_tools_for_frontend(user_tier, user_id)
    target_tool = next((tool for tool in tools if tool.get("tool_id") == tool_id), None)
    if target_tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if target_tool.get("locked"):
        raise HTTPException(status_code=403, detail="Your current membership tier cannot configure this tool")

    await tools_repo.update_user_tool_preference(user_id, tool_id, request.is_enabled)

    invalidate_tools_cache(user_id=user_id)

    try:
        from core.agents.bootstrap import invalidate_manager_cache

        invalidate_manager_cache(user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[tools] Failed to invalidate manager cache: {e}")

    return {"success": True, "tool_id": tool_id, "is_enabled": request.is_enabled}


@router.put("/api/tools/{tool_id}/preference")
@limiter.limit("20/minute")
async def set_tool_preference(
    request: Request,
    tool_id: str,
    req: ToolPreferenceRequest,
    current_user: dict = Depends(get_current_user),
):
    return await _set_tool_preference_impl(tool_id, req, current_user)


@router.put("/api/user/tools/{tool_id}/preference")
@limiter.limit("20/minute")
async def set_user_tool_preference(
    request: Request,
    tool_id: str,
    req: ToolPreferenceRequest,
    current_user: dict = Depends(get_current_user),
):
    return await _set_tool_preference_impl(tool_id, req, current_user)
