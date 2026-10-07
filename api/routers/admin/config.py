"""
Admin System Config Management
Configuration and audit log endpoints
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import require_admin
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.database.connection import get_connection
from core.database.system_config import list_all_configs_with_metadata, set_config

from .schemas import UpdateConfigRequest

router = APIRouter(tags=["Admin - Config"])

# 資料庫裡還在、但沒有任何程式讀的設定（2026-09-27 盤點）：留在後台只會讓人以為改了有效。
# 實際價格讀環境變數（core/config.py 的 PREMIUM_USD_PRICES／FORUM_*_USD），在「設定中心」看得到。
# 列留著不刪（price_premium 仍是舊付款紀錄的 fallback 金額，屬金流程式，不在這次範圍）。
HIDDEN_CONFIG_KEYS = frozenset(
    {"price_create_post", "price_premium", "price_tip", "scam_list_page_size"}
)


# ---------------------------------------------------------------------------
# Sync DB helpers — run on a worker thread via run_sync, never inline on the
# event loop. AGENTS.md forbids synchronous DB I/O directly in async routes.
# ---------------------------------------------------------------------------


def _admin_get_config_audit_sync(limit):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT config_key, old_value, new_value, changed_by, changed_at
                FROM config_audit_log
                ORDER BY changed_at DESC
                LIMIT %s
            """,
                (limit,),
            )
            rows = c.fetchall()
            logs = [
                {
                    "key": r[0],
                    "old_value": r[1],
                    "new_value": r[2],
                    "changed_by": r[3],
                    "changed_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
        return {"success": True, "logs": logs}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Async route handlers — delegate DB work to run_sync (worker thread).
# ---------------------------------------------------------------------------


@router.get("/config/all")
async def admin_get_all_configs(admin_user: dict = Depends(require_admin)):
    """獲取所有系統設定（依類別分組）"""
    configs = [
        cfg
        for cfg in await run_sync(list_all_configs_with_metadata)
        if cfg.get("key") not in HIDDEN_CONFIG_KEYS
    ]

    # Group by category
    grouped = {}
    for cfg in configs:
        cat = cfg.get("category", "general")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(cfg)

    return {"success": True, "configs_by_category": grouped}


@router.put("/config/{key}")
@limiter.limit("20/minute")
async def admin_update_config(
    key: str, request: Request, body: UpdateConfigRequest, admin_user: dict = Depends(require_admin)
):
    """更新單一設定值"""
    if key in HIDDEN_CONFIG_KEYS:
        raise HTTPException(status_code=404, detail="This setting is not used")
    success = await run_sync(
        lambda: set_config(key, body.value, changed_by=admin_user["user_id"])
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update config")

    return {"success": True, "key": key, "value": body.value}


@router.get("/config/audit")
async def admin_get_config_audit(
    limit: int = Query(50, ge=1, le=200), admin_user: dict = Depends(require_admin)
):
    """獲取設定變更歷史"""
    try:
        return await run_sync(_admin_get_config_audit_sync, limit)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to query config change history")
