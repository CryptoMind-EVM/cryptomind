"""綁定錢包的鏈上持倉與帳本同步 API（錢包分頁、帳本「已驗證持倉」卡）。

- ``GET  /api/wallet/holdings``：每個綁定錢包的餘額（含 USD 估值）＋合計
- ``GET  /api/journal/onchain/status``：自動同步開關、上次同步、上次結果
- ``PUT  /api/journal/onchain/status``：開關
- ``POST /api/journal/onchain/sync``：立即同步（1 次／分）

設計：docs/plans/2026-09-12-wallet-ledger-calendar-integration.md §2
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_user
from api.funnel import record_journal_first_entry_if_first
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.onchain import store
from core.onchain.holdings import collect_holdings
from core.onchain.sync import sync_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onchain"])


class SyncStatusInput(BaseModel):
    enabled: bool


def _status_payload(user_id: str) -> dict:
    status = store.get_status(user_id)
    last = status.get("last_synced_at")
    return {
        "enabled": status["enabled"],
        "last_synced_at": last.isoformat() if hasattr(last, "isoformat") else last,
        "last_result": status.get("last_result"),
        "wallets": store.list_wallets(user_id),
    }


@router.get("/api/wallet/holdings")
@limiter.limit("20/minute")
async def wallet_holdings(
    request: Request, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        data = await run_sync(lambda: collect_holdings(user_id))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[onchain] holdings failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load wallet holdings")
    return {"success": True, **data}


@router.get("/api/journal/onchain/status")
async def onchain_status(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    try:
        return {"success": True, **(await run_sync(lambda: _status_payload(user_id)))}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[onchain] status failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load sync status")


@router.put("/api/journal/onchain/status")
async def set_onchain_status(
    body: SyncStatusInput, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        await run_sync(lambda: store.set_enabled(user_id, body.enabled))
        return {"success": True, **(await run_sync(lambda: _status_payload(user_id)))}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[onchain] set status failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save sync status")


@router.post("/api/journal/onchain/sync")
@limiter.limit("1/minute")
async def onchain_sync_now(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """手動同步：沒綁定錢包 → 400 引導去綁；有 → 跑一次並回結果。"""
    user_id = current_user["user_id"]
    wallets = await run_sync(lambda: store.list_wallets(user_id))
    if not wallets:
        raise HTTPException(
            status_code=400, detail="No wallet bound. Bind a wallet in Settings first."
        )
    try:
        result = await run_sync(lambda: sync_user(user_id, wallets=wallets))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[onchain] sync failed user=%s: %s", user_id, exc)
        raise HTTPException(
            status_code=500, detail="On-chain sync failed, please try again later"
        )
    # 轉換漏斗（PR-5）：手動同步帶進第一批帳也算第一次記帳
    added = int(result.get("added") or 0) if isinstance(result, dict) else 0
    await record_journal_first_entry_if_first(user_id, "onchain", added=added)
    return {
        "success": True,
        "result": result,
        **(await run_sync(lambda: _status_payload(user_id))),
    }
