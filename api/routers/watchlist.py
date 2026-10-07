"""使用者自選清單（2026-09-27；core/database/trading.py，c056）。

使用者自己挑的標的：早報的「👀 自選清單」每天列價格與漲跌、行事曆同步抓財報／解鎖事件、
設定頁每一檔都能直接設價格警報。市場＝core/database/trading.WATCHLIST_MARKETS（10 個）。

加入前先抓一次報價——抓不到就不收（打錯代號不會默默躺在清單裡，早報也不會一直空著）。
"""

from __future__ import annotations

import asyncio
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core.database import trading

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])

Market = Literal[
    "crypto",
    "tw_stock",
    "us_stock",
    "hk_stock",
    "jp_stock",
    "kr_stock",
    "cn_stock",
    "in_stock",
    "commodity",
    "forex",
]


class WatchlistItemInput(BaseModel):
    market: Market  # 與 core/database/trading.WATCHLIST_MARKETS 一致（測試盯著）
    symbol: str = Field(..., min_length=1, max_length=24)


@router.get("")
async def get_user_watchlist(current_user: dict = Depends(get_current_user)):
    try:
        items = await run_sync(trading.get_watchlist, current_user["user_id"])
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[watchlist] load failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to fetch watchlist")
    return {
        "items": items,
        "max": trading.MAX_WATCHLIST,
        "markets": list(trading.WATCHLIST_MARKETS),
    }


@router.post("/add")
@limiter.limit("20/minute")
async def add_watchlist(
    request: Request,
    body: WatchlistItemInput,
    current_user: dict = Depends(get_current_user),
):
    symbol = trading.normalize_symbol(body.market, body.symbol)
    if not symbol:
        raise HTTPException(status_code=422, detail="Invalid symbol format")
    user_id = current_user["user_id"]
    items = await run_sync(trading.get_watchlist, user_id)
    item = {"market": body.market, "symbol": symbol}
    if item in items:
        return {"success": True, "added": False, "item": item}
    if len(items) >= trading.MAX_WATCHLIST:
        raise HTTPException(
            status_code=400,
            detail=f"Watchlist is full (max {trading.MAX_WATCHLIST}). Remove one first.",
        )

    from api.alert_checker import _fetch_price

    quote = await _fetch_price(symbol, body.market)
    if not quote:
        raise HTTPException(status_code=404, detail="Symbol not found")
    try:
        await run_sync(trading.add_to_watchlist, user_id, body.market, symbol)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[watchlist] add failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to add")
    return {"success": True, "added": True, "item": item, "price": quote[0]}


class WatchlistMarketInput(BaseModel):
    symbols: List[str] = Field(default_factory=list, max_length=100)


@router.put("/{market}")
@limiter.limit("30/minute")
async def put_market_watchlist(
    market: Market,
    request: Request,
    body: WatchlistMarketInput,
    current_user: dict = Depends(get_current_user),
):
    """各市場分頁同步用：這個市場的自選整批設成 body.symbols（其他市場不動）。

    代號來自分頁自己的清單（畫面上本來就有報價），這裡只檢查格式、不逐檔查價；
    格式不合的丟掉。總數超過上限時只收得下的前幾檔，回 truncated=true。
    """
    normalized: list = []
    for raw in body.symbols:
        sym = trading.normalize_symbol(market, raw)
        if sym and sym not in normalized:
            normalized.append(sym)
    user_id = current_user["user_id"]
    try:
        items = await run_sync(trading.get_watchlist, user_id)
        room = max(trading.MAX_WATCHLIST - sum(1 for i in items if i["market"] != market), 0)
        truncated = len(normalized) > room
        normalized = normalized[:room]
        await run_sync(trading.replace_market, user_id, market, normalized)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[watchlist] replace failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to save watchlist")
    return {"success": True, "market": market, "symbols": normalized, "truncated": truncated}


@router.post("/remove")
@limiter.limit("20/minute")
async def remove_watchlist(
    request: Request,
    body: WatchlistItemInput,
    current_user: dict = Depends(get_current_user),
):
    symbol = trading.normalize_symbol(body.market, body.symbol) or body.symbol.upper()
    try:
        removed = await run_sync(
            trading.remove_from_watchlist, current_user["user_id"], body.market, symbol
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[watchlist] remove failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to remove")
    return {"success": True, "removed": removed}
