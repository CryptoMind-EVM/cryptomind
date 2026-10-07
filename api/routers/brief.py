"""每日早報偏好 API（Settings 用）。

- ``GET  /api/user/brief-prefs``：生效偏好（沒設過就回預設：綁 Telegram 的人開、08:00）
- ``PUT  /api/user/brief-prefs``：儲存
- ``POST /api/user/brief-prefs/preview``：用現在的資料組一份給使用者看（不送、不記）
- ``GET  /api/user/brief-prefs/symbols``：持倉清單＋早報不列的標的（設定頁的開關用）
- ``PUT  /api/user/brief-prefs/symbols``：某一檔要不要出現在早報（c057，不動自選／帳本）

設計：docs/plans/2026-09-12-daily-brief-retention-design.md
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.deps import get_current_user
from api.funnel import record_brief_enabled
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.daily_brief import store
from core.daily_brief.schedule import VALID_CHANNELS, effective_prefs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/brief-prefs", tags=["daily-brief"])


class BriefPrefsInput(BaseModel):
    enabled: bool
    send_hour: int = Field(default=8, ge=0, le=23)
    timezone: str = Field(default="Asia/Taipei", min_length=1, max_length=64)
    channels: List[str] = Field(
        default_factory=lambda: ["telegram", "inapp"], max_length=4
    )
    include_spend: bool = True
    include_macro: bool = True

    @field_validator("timezone")
    @classmethod
    def _valid_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("unknown timezone") from exc
        return v

    @field_validator("channels")
    @classmethod
    def _valid_channels(cls, v: List[str]) -> List[str]:
        cleaned = [c for c in dict.fromkeys(v) if c in VALID_CHANNELS]
        if not cleaned:
            raise ValueError("at least one channel")
        return cleaned


class BriefSymbolInput(BaseModel):
    # 持倉的市場來自帳本，不限自選那 10 個；只擋怪字元
    market: str = Field(..., min_length=2, max_length=20, pattern=r"^[a-z][a-z_]*$")
    symbol: str = Field(
        ..., min_length=1, max_length=24, pattern=r"^[A-Za-z0-9][A-Za-z0-9.=&^_-]*$"
    )
    hidden: bool


def _brief_symbols(user_id: str) -> dict:
    """持倉（帳本，數量 > 0，同市場同代號只列一次）＋早報不列的標的。"""
    from core.orm.trade_journal_repo import get_journal_repo

    positions = []
    seen = set()
    try:
        rows = get_journal_repo(user_id).get_positions()
    except Exception as exc:  # noqa: BLE001 — 帳本讀不到，開關清單就只剩自選
        logger.warning("[brief] positions failed user=%s: %s", user_id, exc)
        rows = []
    for p in rows:
        if float(p.get("quantity") or 0) <= 0 or not p.get("symbol"):
            continue
        key = (p.get("market") or "", str(p["symbol"]).upper())
        if key in seen:
            continue
        seen.add(key)
        positions.append({"market": key[0], "symbol": key[1]})
    hidden = sorted(store.hidden_symbols(user_id))
    return {
        "positions": positions,
        "hidden": [{"market": m, "symbol": sym} for m, sym in hidden],
    }


def _load(user_id: str) -> dict:
    row = store.get_prefs_row(user_id)
    if not row:
        # 使用者存在但 JOIN 查不到（極少見：is_active=false）→ 回關閉的預設
        row = {
            "user_id": user_id,
            "language": "zh-TW",
            "telegram_id": None,
            "prefs_user_id": None,
        }
    prefs = effective_prefs(row).to_api()
    # Base App／Farcaster 宿主推播：有存到 token 的人設定頁才顯示這個頻道
    try:
        from core.miniapp_notifications import service as notif_service
        from core.miniapp_notifications import store as notif_store

        prefs["baseapp_available"] = bool(
            notif_store.status_for_user(user_id).get("enabled")
        ) or notif_service.base_app_opted_in(user_id)
    except Exception:  # noqa: BLE001 — 表還沒建／查詢失敗不影響早報設定
        prefs["baseapp_available"] = False
    return prefs


def _enabled_now(user_id: str) -> bool:
    """目前的生效開關（沒設過偏好就照預設：綁 Telegram 的人開）。"""
    row = store.get_prefs_row(user_id)
    return bool(row) and effective_prefs(row).enabled


async def _enabled_before_save(user_id: str) -> Optional[bool]:
    """轉換漏斗用：存檔前的狀態；查不到回 None（不記事件、也不擋存檔）。"""
    try:
        return await run_sync(_enabled_now, user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[brief] read state before save failed user=%s: %s", user_id, exc
        )
        return None


@router.get("")
@limiter.limit("30/minute")
async def get_brief_prefs(
    request: Request, current_user: dict = Depends(get_current_user)
):
    try:
        return {
            "success": True,
            "prefs": await run_sync(_load, current_user["user_id"]),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[brief] load prefs failed user=%s: %s", current_user.get("user_id"), exc
        )
        raise HTTPException(status_code=500, detail="Failed to load brief preferences")


@router.put("")
@limiter.limit("20/minute")
async def put_brief_prefs(
    request: Request,
    body: BriefPrefsInput,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    was_enabled = await _enabled_before_save(user_id)
    try:
        await run_sync(
            lambda: store.upsert_prefs(
                user_id,
                enabled=body.enabled,
                send_hour=body.send_hour,
                timezone=body.timezone,
                channels=body.channels,
                include_spend=body.include_spend,
                include_macro=body.include_macro,
            )
        )
        if body.enabled and was_enabled is False:
            record_brief_enabled(user_id)  # 轉換漏斗（PR-5）：關 → 開
        return {"success": True, "prefs": await run_sync(_load, user_id)}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[brief] save prefs failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save brief preferences")


@router.get("/symbols")
@limiter.limit("30/minute")
async def get_brief_symbols(
    request: Request, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        return {"success": True, **await run_sync(_brief_symbols, user_id)}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[brief] load symbols failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load brief symbols")


@router.put("/symbols")
@limiter.limit("60/minute")
async def put_brief_symbol(
    request: Request,
    body: BriefSymbolInput,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    try:
        ok = await run_sync(
            store.set_symbol_hidden, user_id, body.market, body.symbol, body.hidden
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[brief] save symbol failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save brief symbol")
    if not ok:
        raise HTTPException(status_code=400, detail="Too many hidden symbols")
    return {
        "success": True,
        "market": body.market,
        "symbol": body.symbol.upper(),
        "hidden": body.hidden,
    }


@router.post("/preview")
@limiter.limit("5/minute")
async def preview_brief(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """組一份現在的早報給使用者看（不叫 LLM、不送、不記 last_sent_on）。"""
    from core.daily_brief.collect import collect_brief_data
    from core.daily_brief.compose import compose_brief

    user_id = current_user["user_id"]
    try:
        row = await run_sync(store.get_prefs_row, user_id)
        if not row:
            raise HTTPException(status_code=404, detail="user not found")
        prefs = effective_prefs(row)
        data = await collect_brief_data(prefs, datetime.now(timezone.utc))
        text = compose_brief(data)
        return {"success": True, "text": text, "empty": text is None}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[brief] preview failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to build preview")
