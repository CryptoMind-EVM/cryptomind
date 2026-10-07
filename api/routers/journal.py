"""Unified Ledger REST API — 統一帳本（投資＋支出＋收入）。

Endpoints:
  GET    /api/journal/entries   — 列出（支援篩選/分頁）
  POST   /api/journal/entry     — 手動新增（表單用）
  PUT    /api/journal/entry/{id} — 修改
  DELETE /api/journal/entry/{id} — 刪除（soft delete，c035）
  GET    /api/journal/summary   — 類別彙總（Dashboard 用）
  GET    /api/journal/positions — 持倉＋損益（UX 第二輪）
  GET    /api/journal/entry/{id}/revisions — 修訂時間線（c037）
  POST   /api/journal/entry/{id}/restore   — 還原修訂／救回（c037）
  GET    /api/journal/deleted   — 回收筒（c037）
  GET    /api/journal/base-currency — 讀報表基準幣（c039）
  PUT    /api/journal/base-currency — 切換基準幣（整批搬移換算值，c039）

2026-08-24 review 修正：
- repo（sync DB + 可能的同步匯率 httpx 5s）改經 run_sync 進 executor，
  不得阻塞 event loop（production 單 worker，AGENTS.md 禁止 async 內同步 I/O）
- Pydantic 邊界補齊：幣別/類型/side pattern、長度上限、limit/offset ge/le
- traded_at 格式錯誤回 422（原為 500）

2026-08-27 自填匯率：
- create/update 皆接受 exchange_rate（自填匯率）——匯率服務抓不到時的
  出口，也供使用者填自己的實際成交匯率；標記 rate_source=manual

2026-08-26 review 修正：
- repo「database error」改映射 500（基礎設施故障不該以 4xx 呈現，
  ops 告警以 5xx 為錨）；「conflict」（修訂號併發競態）映射 409
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.funnel import record_journal_first_entry_if_first
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.audit import audit_log

logger = logging.getLogger(__name__)
router = APIRouter()


def _uid(current_user: dict) -> str:
    return current_user.get("user_id") or current_user.get("uid", "")


def _repo(current_user: dict):
    from core.orm.trade_journal_repo import TradeJournalRepo

    return TradeJournalRepo(_uid(current_user))


# ── GET /api/journal/entries ─────────────────────────────────


@router.get("/api/journal/entries")
@limiter.limit("30/minute")
async def list_entries(
    request: Request,
    entry_type: Optional[str] = Query(
        None,
        description="單值或逗號子集（expense,income）——UX 第二輪收支視圖",
        pattern="^(trade|expense|income)(,(trade|expense|income))*$",
    ),
    category: Optional[str] = None,
    symbol: Optional[str] = None,
    search: Optional[str] = Query(None, max_length=100),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """列出帳本條目（支援篩選/分頁）。

    totals：同一組篩選下「全部」條目依 entry_type 的金額合計——帳本頁的
    總支出／總收入卡以前拿這頁列表（limit 100）加總，超過就少算。
    """
    try:
        repo = _repo(current_user)
        types = entry_type.split(",") if entry_type else None
        filters = dict(
            entry_type=types,
            category=category,
            symbol=symbol,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        entries = await run_sync(
            partial(repo.list_trades, **filters, limit=limit, offset=offset)
        )
        totals = await run_sync(partial(repo.get_entry_totals, **filters))
        return {"entries": entries, "count": len(entries), "totals": totals}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[journal] list failed for %s: %s", _uid(current_user), exc)
        raise HTTPException(status_code=500, detail="Failed to list entries") from exc


# ── POST /api/journal/entry ──────────────────────────────────


class JournalEntryInput(BaseModel):
    entry_type: str = Field(
        default="expense",
        description="trade/expense/income",
        pattern="^(trade|expense|income)$",
    )
    symbol: str = Field(default="", description="標的或幣別", max_length=30)
    market: str = Field(default="", description="市場", max_length=15)
    amount: float = Field(gt=0, le=1e12, description="金額")
    currency: str = Field(
        default="TWD",
        description="計價貨幣（ISO 4217 / 幣別 ticker）",
        min_length=1,
        max_length=8,
        pattern="^[A-Za-z]+$",
    )
    quantity: float = Field(default=1, gt=0, le=1e12, description="數量")
    side: str = Field(default="buy", description="buy/sell", pattern="^(buy|sell)$")
    category: str = Field(default="", description="類別（留空自動推斷）", max_length=20)
    note: str = Field(default="", description="備註", max_length=200)
    traded_at: Optional[str] = Field(
        default=None, max_length=40, description="交易時間 ISO（留空=現在）"
    )
    exchange_rate: Optional[float] = Field(
        default=None,
        gt=0,
        le=1e12,
        description="自填匯率（1 單位計價幣 = ? 基準幣；留空＝自動抓取）",
    )


@router.post("/api/journal/entry")
@limiter.limit("20/minute")
async def create_entry(
    request: Request,
    body: JournalEntryInput,
    current_user: dict = Depends(get_current_user),
):
    """手動新增一筆帳本條目（Dashboard 表單用）。"""
    try:
        repo = _repo(current_user)
        traded_at = None
        if body.traded_at:
            try:
                traded_at = datetime.fromisoformat(
                    body.traded_at.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail="traded_at must be ISO 8601"
                ) from exc
        result = await run_sync(
            partial(
                repo.add_entry,
                entry_type=body.entry_type,
                symbol=body.symbol,
                market=body.market,
                side=body.side,
                quantity=body.quantity,
                price=body.amount,
                currency=body.currency,
                category=body.category,
                note=body.note,
                exchange_rate=body.exchange_rate,
                traded_at=traded_at,
                source="manual",
            )
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Failed to create entry"),
            )
        # 轉換漏斗（PR-5）：第一筆帳＝啟用
        await record_journal_first_entry_if_first(_uid(current_user), "manual")
        return {"ok": True, "id": result.get("id")}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[journal] create failed for %s: %s", _uid(current_user), exc)
        raise HTTPException(status_code=500, detail="Failed to create entry") from exc


# ── PUT /api/journal/entry/{id} ───────────────────────────────


def _repo_error_status(result: dict) -> int:
    """repo ok=False 的錯誤 → HTTP 狀態：database error=500（ops 告警錨）、
    conflict（併發修訂競態）=409、其餘（not found / rate unavailable 等
    使用者可解者）=400。"""
    err = result.get("error", "")
    if err == "database error":
        return 500
    if err == "conflict":
        return 409
    return 400


class JournalEntryUpdate(BaseModel):
    amount: Optional[float] = Field(
        default=None, gt=0, le=1e12, description="金額（改了會重算匯率）"
    )
    currency: Optional[str] = Field(
        default=None,
        description="計價貨幣",
        min_length=1,
        max_length=8,
        pattern="^[A-Za-z]+$",
    )
    category: Optional[str] = Field(default=None, description="類別", max_length=20)
    note: Optional[str] = Field(default=None, description="備註", max_length=200)
    exchange_rate: Optional[float] = Field(
        default=None,
        gt=0,
        le=1e12,
        description="自填匯率（留空＝沿用凍結匯率／必要時自動抓取）",
    )


@router.put("/api/journal/entry/{entry_id}")
@limiter.limit("20/minute")
async def update_entry(
    request: Request,
    entry_id: int,
    body: JournalEntryUpdate,
    current_user: dict = Depends(get_current_user),
):
    """修改一筆帳本條目（金額/幣別/類別/備註）。"""
    try:
        repo = _repo(current_user)
        result = await run_sync(
            partial(
                repo.update_entry,
                entry_id,
                amount=body.amount,
                currency=body.currency,
                category=body.category,
                note=body.note,
                exchange_rate=body.exchange_rate,
            )
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=_repo_error_status(result),
                detail=result.get("error", "Update failed"),
            )
        audit_log(
            "journal_entry_update",
            user_id=_uid(current_user),
            resource_type="journal_entry",
            resource_id=str(entry_id),
            metadata={
                "fields": sorted(
                    k for k, v in body.model_dump(exclude_none=True).items()
                )
            },
        )
        return {"ok": True, "id": entry_id}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[journal] update failed for %s: %s", _uid(current_user), exc)
        raise HTTPException(status_code=500, detail="Failed to update") from exc


# ── DELETE /api/journal/entry/{id} ───────────────────────────


@router.delete("/api/journal/entry/{entry_id}")
@limiter.limit("20/minute")
async def delete_entry(
    request: Request,
    entry_id: int,
    current_user: dict = Depends(get_current_user),
):
    """刪除一筆帳本條目（soft delete——標記 deleted_at，c035）。"""
    try:
        repo = _repo(current_user)
        result = await run_sync(partial(repo.delete_trade, entry_id))
        if not result.get("ok"):
            raise HTTPException(
                status_code=_repo_error_status(result),
                detail=result.get("error", "Delete failed"),
            )
        audit_log(
            "journal_entry_delete",
            user_id=_uid(current_user),
            resource_type="journal_entry",
            resource_id=str(entry_id),
        )
        return {"ok": True, "deleted": entry_id}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[journal] delete failed for %s: %s", _uid(current_user), exc)
        raise HTTPException(status_code=500, detail="Failed to delete") from exc


# ── GET /api/journal/entry/{id}/revisions ─────────────────────


@router.get("/api/journal/entry/{entry_id}/revisions")
@limiter.limit("30/minute")
async def list_revisions(
    request: Request,
    entry_id: int,
    current_user: dict = Depends(get_current_user),
):
    """條目修訂時間線（新到舊；c037 版本史）。"""
    try:
        repo = _repo(current_user)
        revisions = await run_sync(partial(repo.list_revisions, entry_id))
        return {"revisions": revisions, "count": len(revisions)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "[journal] revisions failed for %s: %s", _uid(current_user), exc
        )
        raise HTTPException(
            status_code=500, detail="Failed to list revisions"
        ) from exc


# ── POST /api/journal/entry/{id}/restore ──────────────────────


class JournalRestoreInput(BaseModel):
    revision_no: Optional[int] = Field(
        default=None, ge=1, description="還原目標修訂（省略＝刪除前狀態，回收筒用）"
    )


@router.post("/api/journal/entry/{entry_id}/restore")
@limiter.limit("20/minute")
async def restore_entry(
    request: Request,
    entry_id: int,
    body: JournalRestoreInput,
    current_user: dict = Depends(get_current_user),
):
    """還原條目到指定修訂（省略 revision_no＝刪除前狀態；可救回已刪條目）。"""
    try:
        repo = _repo(current_user)
        result = await run_sync(
            partial(repo.restore_entry, entry_id, revision_no=body.revision_no)
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=_repo_error_status(result),
                detail=result.get("error", "Restore failed"),
            )
        audit_log(
            "journal_entry_restore",
            user_id=_uid(current_user),
            resource_type="journal_entry",
            resource_id=str(entry_id),
            metadata={"revision_no": body.revision_no},
        )
        return {
            "ok": True,
            "id": entry_id,
            "restored_fields": result.get("restored_fields"),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[journal] restore failed for %s: %s", _uid(current_user), exc)
        raise HTTPException(status_code=500, detail="Failed to restore") from exc


# ── GET /api/journal/deleted ──────────────────────────────────


@router.get("/api/journal/deleted")
@limiter.limit("30/minute")
async def list_deleted(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """回收筒：已 soft-delete 條目列表。"""
    try:
        repo = _repo(current_user)
        entries = await run_sync(
            partial(repo.list_deleted, limit=limit, offset=offset)
        )
        return {"entries": entries, "count": len(entries)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "[journal] deleted list failed for %s: %s", _uid(current_user), exc
        )
        raise HTTPException(
            status_code=500, detail="Failed to list deleted"
        ) from exc


# ── GET /api/journal/positions ────────────────────────────────


@router.get("/api/journal/positions")
@limiter.limit("30/minute")
async def list_positions(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """投資持倉（加權平均成本＋現價＋損益；UX 第二輪投資視圖用）。

    取得到現價的 symbol 才有 unrealized_pnl/current_price。
    build_price_lookup 是同步 httpx——整段經 run_sync 進 executor。
    """

    def _compute():
        from core.tools.crypto_modules.trade_journal import build_price_lookup

        repo = _repo(current_user)
        trades = repo.list_trades(entry_type="trade", limit=10000)
        return repo.get_positions(price_lookup=build_price_lookup(trades))

    try:
        positions = await run_sync(_compute)
        return {"positions": positions, "count": len(positions)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "[journal] positions failed for %s: %s", _uid(current_user), exc
        )
        raise HTTPException(
            status_code=500, detail="Failed to list positions"
        ) from exc


# ── GET /api/journal/summary ─────────────────────────────────


@router.get("/api/journal/summary")
@limiter.limit("30/minute")
async def get_summary(
    request: Request,
    entry_type: Optional[str] = Query(
        None,
        description="單值或逗號子集（expense,income）",
        pattern="^(trade|expense|income)(,(trade|expense|income))*$",
    ),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """類別彙總（Dashboard 圓餅圖用）。"""
    try:
        repo = _repo(current_user)
        types = entry_type.split(",") if entry_type else None
        summary = await run_sync(
            partial(
                repo.get_category_summary,
                entry_type=types,
                date_from=date_from,
                date_to=date_to,
            )
        )
        return {"summary": summary}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[journal] summary failed for %s: %s", _uid(current_user), exc)
        raise HTTPException(status_code=500, detail="Failed to get summary") from exc


# ── 報表基準幣（c039）─────────────────────────────────────────


class BaseCurrencyUpdate(BaseModel):
    currency: str = Field(
        description="新的報表基準幣（TWD/USD/HKD/JPY/CNY/EUR）",
        min_length=1,
        max_length=8,
        pattern="^[A-Za-z]+$",
    )


@router.get("/api/journal/base-currency")
@limiter.limit("30/minute")
async def get_base_currency(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """讀取目前的報表基準幣與可選清單。"""
    try:
        repo = _repo(current_user)
        current = await run_sync(lambda: repo.base_currency)
        return {
            "base_currency": current,
            "supported": sorted(repo.VALID_BASE_CURRENCIES),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "[journal] base currency read failed for %s: %s", _uid(current_user), exc
        )
        raise HTTPException(
            status_code=500, detail="Failed to read base currency"
        ) from exc


@router.put("/api/journal/base-currency")
@limiter.limit("6/minute")
async def set_base_currency(
    request: Request,
    body: BaseCurrencyUpdate,
    current_user: dict = Depends(get_current_user),
):
    """切換報表基準幣——既有條目的換算值整批搬到新基準幣（同一交易）。

    金額本身（price/currency/quantity）不動，只搬 converted_amount /
    exchange_rate 這兩個派生值；交叉匯率抓不到就整筆不做（fail-closed）。
    限流較嚴（6/min）：這是會動到整本帳的操作。
    """
    try:
        repo = _repo(current_user)
        result = await run_sync(partial(repo.rebase_to, body.currency))
        if not result.get("ok"):
            raise HTTPException(
                status_code=_repo_error_status(result),
                detail=result.get("error", "Failed to change base currency"),
            )
        audit_log(
            "journal_base_currency_change",
            user_id=_uid(current_user),
            resource_type="journal",
            resource_id=_uid(current_user),
            metadata={
                "base_currency": result.get("base_currency"),
                "rebased": result.get("rebased"),
                "rate": result.get("rate"),
                "stale": result.get("stale"),
            },
        )
        return {
            "ok": True,
            "base_currency": result.get("base_currency"),
            "rebased": result.get("rebased", 0),
            "rate": result.get("rate"),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "[journal] base currency change failed for %s: %s", _uid(current_user), exc
        )
        raise HTTPException(
            status_code=500, detail="Failed to change base currency"
        ) from exc
