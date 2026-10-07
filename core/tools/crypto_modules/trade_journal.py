"""Unified Ledger Agent Tools — 統一帳本（投資＋支出＋收入）。

設計：docs/plans/2026-08-21-investment-journal-design.md（DANNY Approve 2026-08-21）

record_entry      — 提議記一筆（trade/expense/income），自動分類＋匯率凍結，
                    回傳 __needs_consent__ marker——claw_loop 攔截後彈確認卡，
                    使用者核准才寫入（Propose → Confirm → Commit，金流 HITL）
query_ledger      — 查詢（by 類別/symbol/期間/幣別），支援彙總
get_portfolio_pnl — 投資持倉損益（trade only）
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 與 remember_tool / claw_loop._extract_consent_signal 相同的 marker key
NEEDS_CONSENT_KEY = "__needs_consent__"


def _finite_or_none(value) -> Optional[float]:
    """非有限浮點（NaN/Infinity）→ None。marker 數字欄位共用防護。"""
    if value is None:
        return None
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    return fv if math.isfinite(fv) else None


def _finite_or(value, default: float) -> float:
    fv = _finite_or_none(value)
    return default if fv is None else fv


# ── 匯率格式化 helper（agent 回覆用）──────────────────────────────


# 基準幣顯示符號（與前端 tab-journal.js 的 BASE_SYMBOLS 一致；
# JPY/CNY 同為 ¥，以 CN¥ 區分，避免兩種幣別看起來一樣）
_BASE_SYMBOLS = {
    "TWD": "NT$", "USD": "$", "HKD": "HK$",
    "JPY": "¥", "CNY": "CN¥", "EUR": "€",
}


def _base_symbol(base: str) -> str:
    return _BASE_SYMBOLS.get(base, base + " ")


def _entry_date_str(ts) -> str:
    """帳本列表的日期（MM/DD）。

    traded_at 是 timestamptz，資料庫給的是 UTC：台灣時間 00:00–08:00 記的帳，直接 strftime 會顯示成
    前一天（2026-10-06 00:34 記的帳列成 10/05，而 AI 在同一段對話說「今天（2026-10-06）」）。
    平台的時間基準是台灣時間（system prompt 的「現在時間」也是 UTC+8，台灣沒有夏令時間），這裡一致換算。
    """
    from datetime import timedelta, timezone

    if hasattr(ts, "strftime"):
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.astimezone(timezone(timedelta(hours=8)))
        return ts.strftime("%m/%d")
    text = "" if ts is None else str(ts)
    # 字串（ISO 8601）：取 MM-DD 轉成 MM/DD；不認得的格式原樣截短，至少不丟例外
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[5:10].replace("-", "/")
    return text[:5]


def _fmt_rate(amount: float, currency: str, base: str = "TWD", rate: float | None = None) -> str:
    """格式化含匯率換算的金額顯示。"""
    from core.tools.crypto_modules.exchange_rate import get_exchange_rate

    r = rate or get_exchange_rate(currency, base)
    if not r or currency == base:
        return f"{amount:g} {currency}"
    converted = amount * r
    sym = _base_symbol(base).rstrip()
    return f"{amount:g} {currency} (~{sym}{converted:,.0f})"


# ── record_entry：統一記帳入口 ──────────────────────────────────


class RecordEntryInput(BaseModel):
    entry_type: str = Field(
        default="expense",
        description="trade=buy/sell of an asset / expense=spending / income=earning"
    )
    symbol: str = Field(
        default="",
        description="Asset ticker or currency (BTC/ETH/TON/2330.TW/AAPL) — empty ok for expense/income"
    )
    market: str = Field(
        default="",
        description="Market: crypto/tw_stock/us_stock/hk_stock/jp_stock/kr_stock/cn_stock/forex/commodity — empty = infer from symbol; empty ok for expense/income"
    )
    amount: float = Field(
        default=0,
        description="Amount: total value for expense/income; unit price for trade",
        gt=0,
    )
    currency: str = Field(
        default="TWD",
        description="Currency of the amount (TWD/USD/USDT/BTC/TON...)"
    )
    quantity: float = Field(
        default=1,
        description="Quantity for trade (coins/shares/lots 台股=張); keep 1 for expense/income",
        gt=0,
    )
    side: str = Field(
        default="buy",
        description="buy/sell (trade only)",
        pattern="^(buy|sell)$",
    )
    instrument_type: str = Field(
        default="spot",
        description="spot/futures/margin (trade only)"
    )
    direction: str = Field(
        default="long",
        description="long/short (trade only)"
    )
    leverage: float = Field(
        default=1,
        description="Leverage multiple, 1 for spot (trade only)",
        ge=1,
    )
    category: str = Field(
        default="",
        description="Category (food/transport/housing/entertainment/medical/shopping/education/investment/income/other) — empty = auto-infer from note"
    )
    fee: float = Field(default=0, description="Transaction fee", ge=0)
    note: str = Field(default="", description="Short note (e.g. 'lunch with coworkers')")
    exchange_rate: Optional[float] = Field(
        default=None,
        gt=0,
        description=(
            "User-stated exchange rate (1 unit of `currency` = ? in the user's "
            "reporting currency). "
            "Only set it when the user states a rate themselves "
            "(e.g. 'I bought USD at 32.1'); leave None to look it up."
        ),
    )


@tool("record_entry", args_schema=RecordEntryInput)
def record_entry(
    entry_type: str = "expense",
    symbol: str = "",
    market: str = "",
    amount: float = 0,
    currency: str = "TWD",
    quantity: float = 1,
    side: str = "buy",
    instrument_type: str = "spot",
    direction: str = "long",
    leverage: float = 1,
    category: str = "",
    fee: float = 0,
    note: str = "",
    exchange_rate: Optional[float] = None,
) -> str:
    """Propose an entry in the user's unified ledger (expense / income / trade).

    Call this for ANY financial transaction the user mentions — a confirmation
    card is shown and the entry is saved ONLY after the user approves.
    Do NOT use memory/remember tools for amounts or transactions.

    Examples:
    "lunch 250" → record_entry(entry_type="expense", amount=250, currency="TWD", note="lunch")
    "spent 0.2 TON" → record_entry(entry_type="expense", amount=0.2, currency="TON")
    "bought 0.5 BTC at 61200" → record_entry(entry_type="trade", symbol="BTC", market="crypto",
        amount=61200, currency="USD", quantity=0.5, side="buy")
    "2330.TW 2 lots at 912" → record_entry(entry_type="trade", symbol="2330.TW", market="tw_stock",
        amount=912, currency="TWD", quantity=2, side="buy")
    "salary 50000" → record_entry(entry_type="income", amount=50000, currency="TWD")
    "10x short ETH futures" → record_entry(entry_type="trade", symbol="ETH", market="crypto",
        instrument_type="futures", direction="short", leverage=10, ...)

    "bought 100 USD at 32.1" → record_entry(entry_type="expense", amount=100,
        currency="USD", exchange_rate=32.1)

    Auto-categorizes expenses and freezes the exchange rate at proposal time
    (pass exchange_rate only when the user states the rate themselves).
    Returns a pending-confirmation marker; tell the user a confirmation card
    was shown and nothing is saved until they approve.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return "Error: login required to use the ledger"

    from core.tools.crypto_modules.exchange_rate import (
        get_exchange_rate,
        infer_category,
    )

    # 類別推斷在「提案時」做——確認卡上要顯示給使用者看
    final_category = category or infer_category(note or symbol or "", entry_type)
    if entry_type == "trade":
        final_category = final_category or "investment"
    elif entry_type == "income":
        final_category = final_category or "income"

    # 匯率凍結在「提案時」——確認卡顯示的換算與核可後寫入的一致。
    # 使用者自己講了匯率就用他的（匯率服務抓不到時的出口）。
    # 基準幣讀使用者設定（c039）：寫死 TWD 會讓基準幣是 USD 的使用者拿到
    # 「對台幣凍結的匯率」卻被存成 USD 基準——整筆換算額都是錯的。
    from core.orm.trade_journal_repo import get_journal_repo

    base = get_journal_repo(user_id).base_currency
    # NaN/Infinity 收斂（2026-09-21 線上案例）：LLM 傳 "nan"、匯率上游壞值
    # 都會讓 marker 帶非有限浮點——json.dumps 輸出 NaN token 後瀏覽器
    # JSON.parse 直接丟 frame，同意卡整張消失。數字收預設值、匯率收
    # None（顯示端本來就當「查不到」）。
    amount = _finite_or(amount, 0.0)
    quantity = _finite_or(quantity, 1.0)
    fee = _finite_or(fee, 0.0)
    leverage = _finite_or(leverage, 1.0)
    manual_rate = _finite_or_none(exchange_rate)
    rate = manual_rate or _finite_or_none(get_exchange_rate(currency, base))
    total = amount * (quantity if entry_type == "trade" else 1)
    converted = _finite_or_none(round(total * rate, 2)) if rate else None

    import hashlib
    import json as _json
    import time as _time

    marker = {
        NEEDS_CONSENT_KEY: True,
        "ts": _time.time(),  # 唯一戳：claw_loop 以此判斷「新 marker」而非 session 單次 flag
        "kind": "journal_entry",
        "entry_type": entry_type,
        "symbol": symbol,
        "market": market,
        "amount": amount,
        "currency": currency,
        "quantity": quantity if entry_type == "trade" else 1,
        "side": side,
        "instrument_type": instrument_type,
        "direction": direction,
        "leverage": leverage,
        "category": final_category,
        "fee": fee,
        "note": note,
        "base_currency": base,
        "exchange_rate": rate,
        "converted_amount": converted,
        "rate_source": "manual" if manual_rate else "frozen",
    }
    # 穩定 identity（內容雜湊、與 ts 無關）：resume 重跑時工具會重新提案，
    # claw_loop 靠 proposal_id 去重，同一筆不會彈第二次卡。
    marker["proposal_id"] = hashlib.sha256(
        _json.dumps(
            {k: v for k, v in marker.items() if k != "ts"},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return json.dumps(marker, ensure_ascii=False)


# ── query_ledger：查詢 ──────────────────────────────────────────


class QueryLedgerInput(BaseModel):
    entry_type: Optional[str] = Field(
        default=None,
        description="Filter by type: trade/expense/income — None=all"
    )
    category: Optional[str] = Field(
        default=None,
        description="Filter by category (food/transport/investment/...)"
    )
    symbol: Optional[str] = Field(
        default=None,
        description="Filter by asset ticker (BTC/TON/2330.TW...)"
    )
    search: Optional[str] = Field(
        default=None,
        description="Keyword search in note or symbol"
    )
    days: int = Field(
        default=30,
        description="Last N days (0=all history)",
        ge=0,
    )
    limit: int = Field(default=20, description="Max entries", ge=1, le=100)


@tool("query_ledger", args_schema=QueryLedgerInput)
def query_ledger(
    entry_type: Optional[str] = None,
    category: Optional[str] = None,
    symbol: Optional[str] = None,
    search: Optional[str] = None,
    days: int = 30,
    limit: int = 20,
) -> str:
    """Query the user's unified ledger (expenses, income, trades) with filters.
    Use when the user asks about their spending, income, trades, or financial
    history. Never answer these from memory — always call this tool.

    Examples:
    "how much did I spend this month" → query_ledger(entry_type="expense", days=30)
    "how much on food" → query_ledger(category="food", days=30)
    "show my BTC trades" → query_ledger(symbol="BTC", entry_type="trade")
    "what did I buy" → query_ledger(entry_type="trade", days=0)
    """
    from datetime import datetime, timedelta, timezone

    from core.database.base import DatabaseError
    from core.orm.trade_journal_repo import get_journal_repo

    user_id = _get_current_user_id()
    if not user_id:
        return "Error: 需登入才能查詢帳本"

    repo = get_journal_repo(user_id)

    date_from = None
    if days > 0:
        date_from = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        entries = repo.list_trades(
            entry_type=entry_type,
            category=category,
            symbol=symbol,
            search=search,
            date_from=date_from,
            limit=limit,
        )
    except DatabaseError:
        # repo 不再吞 DB 錯誤（2026-08-24 review）——工具層轉為使用者可讀訊息
        logger.warning("[query_ledger] db unavailable for %s", user_id)
        return "Error: 帳本服務暫時無法使用，請稍後再試"
    if not entries:
        return "目前沒有符合條件的記錄。"

    # 彙總
    from core.tools.crypto_modules.exchange_rate import CATEGORY_ICONS

    total_by_cat: dict = {}
    lines = []
    base = repo.base_currency
    base_sym = _base_symbol(base)
    for e in entries:
        amount = float(e["price"]) * float(e["quantity"])
        curr = e["currency"]
        cat = e.get("category") or "other"
        icon = CATEGORY_ICONS.get(cat, "📦")

        # 彙總
        cat_key = cat
        if cat_key not in total_by_cat:
            total_by_cat[cat_key] = 0.0
        conv = float(e.get("converted_amount") or 0)
        total_by_cat[cat_key] += conv if conv > 0 else amount

        # 格式化
        ts = e["traded_at"]
        date_str = _entry_date_str(ts)
        type_icon = {"trade": "📈", "expense": "💸", "income": "💰"}.get(
            e.get("entry_type", ""), ""
        )
        # 用該筆凍結的匯率（記錄當下的事實），不是現在的匯率
        frozen = float(e.get("exchange_rate") or 0) or None
        conv_str = _fmt_rate(amount, curr, base, rate=frozen)
        note_str = f" ({e['note']})" if e.get("note") else ""
        # [#id]：delete/update 工具要以 entry id 定位單筆記錄
        lines.append(
            f"[#{e['id']}] {date_str} {type_icon} {icon} {amount:g} {curr} "
            f"({cat}){note_str} — {conv_str}"
        )

    # 彙總摘要
    summary_parts = [
        f"📊 {cat}: {base_sym}{total:,.0f}"
        for cat, total in sorted(total_by_cat.items(), key=lambda x: -x[1])
    ]
    total_all = sum(total_by_cat.values())
    summary = "\n".join(summary_parts)
    return (
        f"📋 共 {len(entries)} 筆記錄：\n"
        + "\n".join(lines)
        + f"\n\n📊 類別彙總（{base}）：\n{summary}\n💵 總計：{base_sym}{total_all:,.0f}"
    )


# ── delete / update：單筆刪改（Propose → Confirm → Commit，2026-08-25）──────
# Web UI（tab-journal + /api/journal/entry/{id}）已有刪改；這兩個工具把同樣
# 能力補給 AI-Agent——只提案（回傳 __needs_consent__ marker），使用者核准
# 後 claw_loop 才呼叫 repo 的 soft delete / update（金流 HITL 同 record_entry）。


def _find_entry_for_proposal(repo, entry_id: int):
    """以 list_trades 找出使用者自己的 entry（repo 查詢本身綁 user_id，
    他人帳目天然查不到）。回 None 表示不存在。list_trades 的 DB 錯誤
    直接拋 DatabaseError（2026-08-24 review）——這裡只接它，其他異常
    不吞（AGENTS.md 禁 except Exception）。"""
    from core.database.base import DatabaseError

    try:
        entries = repo.list_trades(limit=1000)
    except DatabaseError:
        return None
    for e in entries:
        if int(e.get("id", -1)) == int(entry_id):
            return e
    return None


class DeleteLedgerEntryInput(BaseModel):
    entry_id: int = Field(description="The [#id] of the ledger entry to delete (shown by query_ledger)")


@tool("delete_ledger_entry", args_schema=DeleteLedgerEntryInput)
def delete_ledger_entry(entry_id: int) -> str:
    """Propose deleting ONE entry from the user's unified ledger (soft delete).

    Only PROPOSES — a confirmation card is shown and the entry is deleted
    ONLY after the user approves. Find the entry's [#id] with query_ledger
    first. Use when the user explicitly asks to remove/correct a wrongly
    recorded entry (e.g. a duplicate or a mistake).
    """
    user_id = _get_current_user_id()
    if not user_id:
        return "Error: login required to use the ledger"

    from core.orm.trade_journal_repo import get_journal_repo

    repo = get_journal_repo(user_id)
    entry = _find_entry_for_proposal(repo, entry_id)
    if entry is None:
        return (
            f"Error: no ledger entry with id {entry_id} (it may not exist, "
            "already deleted, or belong to another user). "
            "Call query_ledger to list entries with their [#id]."
        )

    import time as _time

    marker = {
        NEEDS_CONSENT_KEY: True,
        "ts": _time.time(),
        "kind": "journal_delete",
        "entry_id": int(entry_id),
        "before": {
            "id": entry_id,
            "symbol": entry.get("symbol", ""),
            "entry_type": entry.get("entry_type", ""),
            "amount": float(entry.get("price", 0) or 0),
            "currency": entry.get("currency", ""),
            "quantity": float(entry.get("quantity", 1) or 1),
            "category": entry.get("category", ""),
            "note": entry.get("note", ""),
        },
    }
    logger.info(
        "[journal] proposed DELETE (pending consent) user=%s id=%s", user_id, entry_id
    )
    return json.dumps(marker, ensure_ascii=False)


class UpdateLedgerEntryInput(BaseModel):
    entry_id: int = Field(description="The [#id] of the ledger entry to edit (shown by query_ledger)")
    amount: Optional[float] = Field(
        default=None, gt=0, description="New amount/price (None = keep; must be > 0)"
    )
    currency: Optional[str] = Field(default=None, description="New currency code (None = keep)")
    category: Optional[str] = Field(default=None, description="New category (None = keep)")
    note: Optional[str] = Field(default=None, description="New note (None = keep)")
    exchange_rate: Optional[float] = Field(
        default=None,
        gt=0,
        description=(
            "User-stated exchange rate (1 unit of currency = ? in the user's "
            "reporting currency). "
            "Use it when the automatic lookup is unavailable or the user "
            "gives their own rate; None = keep / auto."
        ),
    )


@tool("update_ledger_entry", args_schema=UpdateLedgerEntryInput)
def update_ledger_entry(
    entry_id: int,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    category: Optional[str] = None,
    note: Optional[str] = None,
    exchange_rate: Optional[float] = None,
) -> str:
    """Propose editing ONE entry in the user's unified ledger.

    Only PROPOSES — a confirmation card is shown and the entry is updated
    ONLY after the user approves. Only pass the fields to change (others
    stay as-is). Find the entry's [#id] with query_ledger first. Use for
    corrections the user explicitly requests (wrong amount/typo etc.).
    """
    user_id = _get_current_user_id()
    if not user_id:
        return "Error: login required to use the ledger"

    updates = {
        k: v
        for k, v in {
            "amount": amount,
            "currency": (currency or "").strip().upper() or None,
            "category": (category or "").strip().lower() or None,
            "note": note,
            "exchange_rate": exchange_rate,
        }.items()
        if v is not None
    }
    if not updates:
        return (
            "Error: nothing to update — pass at least one of "
            "amount/currency/category/note/exchange_rate."
        )

    from core.orm.trade_journal_repo import get_journal_repo

    repo = get_journal_repo(user_id)
    entry = _find_entry_for_proposal(repo, entry_id)
    if entry is None:
        return (
            f"Error: no ledger entry with id {entry_id} (it may not exist, "
            "already deleted, or belong to another user). "
            "Call query_ledger to list entries with their [#id]."
        )

    import time as _time

    marker = {
        NEEDS_CONSENT_KEY: True,
        "ts": _time.time(),
        "kind": "journal_update",
        "entry_id": int(entry_id),
        "updates": updates,
        "before": {
            "id": entry_id,
            "symbol": entry.get("symbol", ""),
            "entry_type": entry.get("entry_type", ""),
            "amount": float(entry.get("price", 0) or 0),
            "currency": entry.get("currency", ""),
            "quantity": float(entry.get("quantity", 1) or 1),
            "category": entry.get("category", ""),
            "note": entry.get("note", ""),
        },
    }
    logger.info(
        "[journal] proposed UPDATE (pending consent) user=%s id=%s fields=%s",
        user_id, entry_id, sorted(updates),
    )
    return json.dumps(marker, ensure_ascii=False)


# ── 現價取得：按 market 分流（2026-08-24 補完）─────────────────────# 此前 get_portfolio_pnl 對所有 symbol 都呼叫 get_exchange_rate——它只認得
# 幣種代碼（BTC/TON/USD…），看不懂 AAPL/2330.TW → 股票持倉恆顯「（無現價）」，
# 台股/美股使用者的旗艦功能缺一角（review Important #3）。股票現價改走既有
# provider 層（同步、含 60s 快取、market watch 同源）。

_STOCK_PROVIDER_MARKET = {
    "us_stock": "us",
    "tw_stock": "tw",
    "hk_stock": "hk",
    "jp_stock": "jp",
    "kr_stock": "kr",
    "cn_stock": "cn",
}


def _fetch_stock_current_price(symbol: str, market: str) -> Optional[float]:
    """股票現價（provider 層，同步+快取）。取不到回 None——該持倉不算未實現損益。"""
    provider_market = _STOCK_PROVIDER_MARKET.get(market)
    if not provider_market:
        return None
    try:
        from core.providers import get_provider

        data = get_provider(provider_market).get_price(symbol)
        price = data.get("price")
        return float(price) if price and float(price) > 0 else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        # 單一持倉取價失敗不得炸整個損益查詢——記 log、該標的顯示「無現價」
        logger.warning("[portfolio_pnl] price fetch failed: %s (%s)", symbol, market)
        return None


# ── get_portfolio_pnl（保留，trade only）───────────────────────


class GetPortfolioPnlInput(BaseModel):
    pass


def build_price_lookup(trades: List[Dict]) -> Dict[str, float]:
    """為持倉計算取得現價——agent 工具與 REST positions 端點共用。

    crypto 走匯率/幣價工具（USD 計價），股票走 provider 層；取不到的
    symbol 不放入 lookup（get_positions 對應 unrealized_pnl=None，
    僅顯示成本）。同步 httpx——REST 端點必須經 run_sync 呼叫。
    """
    from core.tools.crypto_modules.exchange_rate import get_exchange_rate

    price_lookup: Dict[str, float] = {}
    for t in trades:
        sym, mkt = t["symbol"], t["market"]
        if sym in price_lookup:
            continue
        if mkt == "crypto":
            rate = get_exchange_rate(sym, "USD")
            if rate and rate > 0:
                price_lookup[sym] = rate
        else:
            price = _fetch_stock_current_price(sym, mkt)
            if price is not None:
                price_lookup[sym] = price
    return price_lookup


@tool("get_portfolio_pnl", args_schema=GetPortfolioPnlInput)
def get_portfolio_pnl() -> str:
    """Calculate the user's portfolio profit/loss (PnL) across all investment positions.
    Use when the user asks about their investment performance."""
    from core.database.base import DatabaseError
    from core.orm.trade_journal_repo import get_journal_repo

    user_id = _get_current_user_id()
    if not user_id:
        return "Error: 需登入才能查詢持倉損益"

    repo = get_journal_repo(user_id)
    try:
        trades = repo.list_trades(entry_type="trade", limit=10000)
    except DatabaseError:
        logger.warning("[get_portfolio_pnl] db unavailable for %s", user_id)
        return "Error: 帳本服務暫時無法使用，請稍後再試"
    if not trades:
        return "目前沒有投資持倉。先記錄一筆交易吧！"

    # 取現價（共用 build_price_lookup——REST positions 端點同路徑）
    price_lookup = build_price_lookup(trades)

    positions = repo.get_positions(price_lookup=price_lookup)
    if not positions:
        return "目前沒有持倉。"

    lines = []
    total_unrealized = 0.0
    total_realized = 0.0
    for p in positions:
        if p["quantity"] <= 0:
            if float(p["realized_pnl"]) != 0:
                total_realized += float(p["realized_pnl"])
            continue

        unit = _market_unit(p["market"])
        lev = f" {p['leverage']:.0f}x" if p["leverage"] > 1 else ""
        direction = "空" if p["direction"] == "short" else ""
        unrealized = p.get("unrealized_pnl")
        current = p.get("current_price")

        if unrealized is not None:
            pnl_str = f"{float(unrealized):+,.0f} {p['currency']}"
            total_unrealized += float(unrealized)
        else:
            pnl_str = "（無現價）"

        lines.append(
            f"{'🟢' if (unrealized or 0) >= 0 else '🔴'} "
            f"{p['symbol']}{lev}{direction} "
            f"{float(p['quantity']):g}{unit} @ {float(p['avg_cost']):,.0f}"
            + (f" → {current:,.0f}" if current else "")
            + f" | {pnl_str}"
        )

    summary = f"\n📊 未實現損益: {total_unrealized:+,.0f}"
    if total_realized:
        summary += f" | 已實現: {total_realized:+,.0f}"
    return "\n".join(lines) + summary


# ── helpers ─────────────────────────────────────────────────────


def _get_current_user_id() -> Optional[str]:
    # 正解在 core.tools.key_resolver（contextvar，由 base_react_agent 執行前注入）。
    # 先前誤 import 不存在的 core.agents.context → except 吞掉 → 永遠 None，
    # record_entry 恆回「需要登入」（2026-08-22 盤點：記帳鏈第三個斷點）。
    from core.tools.key_resolver import get_current_user_id

    return get_current_user_id()


def _market_unit(market: str) -> str:
    return {
        "crypto": "", "tw_stock": "張", "us_stock": "股",
        "hk_stock": "股", "jp_stock": "股", "kr_stock": "股", "cn_stock": "股", "forex": "手",
        "commodity": "口", "cash": "",
    }.get(market, "")


# Export for bootstrap registration
# 保留舊名向後相容（bootstrap 已註冊 record_trade/query_trades）
record_trade = record_entry
query_trades = query_ledger

LEDGER_TOOLS = [record_entry, query_ledger, get_portfolio_pnl]
