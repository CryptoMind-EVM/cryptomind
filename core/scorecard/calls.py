"""判斷評分 v2：明確喊單（設計 §7）。

「我看多 BTC 到下個月」「2330 三個月內會跌」——使用者明講方向與期限，就記一筆
``judgment_calls``，同時在 ``judgment_scores`` 建一列 pending（kind='call'），到期由既有
cron 用收盤價對答案、扣同市場基準，與帳本交易同一套公式。

堵「輸的單不算」：建立後只給 ``CANCEL_WINDOW_HOURS`` 小時反悔（打錯字），之後不能刪；
評過分更不能。進場價取「喊單當天（或最近一個交易日）的收盤價」，不讓使用者自己填。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.database.base import DatabaseBase, transaction
from core.markets import infer_market

from . import prices, rules, store

logger = logging.getLogger(__name__)

SIDES = {
    "bullish": "buy",
    "bearish": "sell",
    "buy": "buy",
    "sell": "sell",
    "long": "buy",
    "short": "sell",
}
MIN_HORIZON = 7
MAX_HORIZON = 365
DEFAULT_HORIZON = 30
CANCEL_WINDOW_HOURS = 24
_PRICE_LOOKBACK_DAYS = 5  # 週末／連假往回找最近一個收盤


class CallError(ValueError):
    """輸入不合法或抓不到進場價（訊息給使用者看）。"""


def normalize_side(side: str) -> str:
    s = (side or "").strip().lower()
    if s not in SIDES:
        raise CallError("side must be bullish or bearish")
    return SIDES[s]


def normalize_horizon(days: Any) -> int:
    try:
        h = int(days) if days not in (None, "") else DEFAULT_HORIZON
    except (TypeError, ValueError) as exc:
        raise CallError("horizon_days must be an integer") from exc
    if not MIN_HORIZON <= h <= MAX_HORIZON:
        raise CallError(f"horizon_days must be between {MIN_HORIZON} and {MAX_HORIZON}")
    return h


def normalize_symbol_market(symbol: str, market: str = "") -> tuple[str, str]:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise CallError("symbol is required")
    mkt = (market or "").strip().lower() or (infer_market(sym) or "")
    if mkt not in rules.BENCHMARKS:
        raise CallError(f"market {mkt or '?'} is not scorable")
    if sym in rules.STABLECOINS:
        raise CallError("stablecoins are not a call")
    return sym, mkt


def latest_close(symbol: str, market: str, on: date) -> Optional[tuple[float, date]]:
    """``on`` 當天沒收盤就往回找最多 _PRICE_LOOKBACK_DAYS 天。"""
    for i in range(_PRICE_LOOKBACK_DAYS + 1):
        d = on - timedelta(days=i)
        px = prices.close_on(symbol, market, d)
        if px:
            return float(px), d
    return None


def pending_row_for_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """一筆喊單 → 一列待評分（純函式；期限就是使用者講的，不像交易固定 30／90）。"""
    entry_date = call["entry_date"]
    if isinstance(entry_date, datetime):
        entry_date = entry_date.date()
    bench = rules.BENCHMARKS.get(call["market"])
    if bench and bench.split(".")[0] == str(call["symbol"]).upper():
        bench = None
    return {
        "entry_id": 0,
        "call_id": int(call["id"]),
        "kind": "call",
        "symbol": str(call["symbol"]).upper(),
        "market": call["market"],
        "side": call["side"],
        "horizon_days": int(call["horizon_days"]),
        "entry_price": float(call["entry_price"]),
        "entry_date": entry_date,
        "exit_date": entry_date + timedelta(days=int(call["horizon_days"])),
        "verification": rules.SELF_REPORTED,
        "bench_symbol": bench,
    }


def create_call(
    user_id: str,
    *,
    symbol: str,
    side: str,
    market: str = "",
    horizon_days: Any = DEFAULT_HORIZON,
    target_price: Optional[float] = None,
    note: str = "",
    source: str = "chat",
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """驗證 → 取進場價 → 寫 judgment_calls ＋ judgment_scores pending。回寫入的列。"""
    sym, mkt = normalize_symbol_market(symbol, market)
    sd = normalize_side(side)
    h = normalize_horizon(horizon_days)
    tp = None
    if target_price not in (None, "", 0):
        try:
            tp = float(target_price)
        except (TypeError, ValueError) as exc:
            raise CallError("target_price must be a number") from exc
        if tp <= 0:
            raise CallError("target_price must be positive")
    on = today or rules.today_utc()
    found = latest_close(sym, mkt, on)
    if not found:
        raise CallError(f"no recent close price for {sym}; cannot record the call")
    entry_price, entry_date = found
    with transaction() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO judgment_calls
                    (user_id, symbol, market, side, horizon_days, target_price,
                     entry_price, entry_date, note, source, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
                RETURNING id, user_id, symbol, market, side, horizon_days, target_price,
                          entry_price, entry_date, note, source, status, created_at
                """,
                (
                    user_id,
                    sym,
                    mkt,
                    sd,
                    h,
                    tp,
                    entry_price,
                    entry_date,
                    (note or "")[:500],
                    source[:32],
                ),
            )
            row = c.fetchone()
            call = {d.name: v for d, v in zip(c.description, row)}
    store.insert_pending(user_id, [pending_row_for_call(call)])
    return _plain(call)


def list_calls(user_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
    rows = DatabaseBase.query_all(
        """
        SELECT c.id, c.symbol, c.market, c.side, c.horizon_days, c.target_price, c.entry_price,
               c.entry_date, c.note, c.source, c.status, c.created_at, c.cancelled_at,
               s.status AS score_status, s.exit_date, s.exit_price, s.raw_pct, s.bench_pct,
               s.excess_pct, s.hit
        FROM judgment_calls c
        LEFT JOIN judgment_scores s
               ON s.user_id = c.user_id AND s.kind = 'call' AND s.call_id = c.id
        WHERE c.user_id = %s
        ORDER BY c.created_at DESC
        LIMIT %s
        """,
        (user_id, int(limit)),
    )
    return [_plain(r) for r in rows]


def cancel_call(
    user_id: str, call_id: int, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """只在 CANCEL_WINDOW_HOURS 內、且還沒評分時可取消；一併刪掉 pending 分數列。"""
    row = DatabaseBase.query_one(
        "SELECT id, status, created_at FROM judgment_calls WHERE id = %s AND user_id = %s",
        (int(call_id), user_id),
    )
    if not row:
        return {"ok": False, "error": "not found"}
    if row["status"] != "open":
        return {"ok": False, "error": f"call is {row['status']}"}
    now = now or datetime.now(timezone.utc)
    created = row["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if now - created > timedelta(hours=CANCEL_WINDOW_HOURS):
        return {"ok": False, "error": "cancel window closed"}
    scored = DatabaseBase.query_one(
        "SELECT 1 FROM judgment_scores WHERE user_id = %s AND kind = 'call' AND call_id = %s "
        "AND status = 'scored'",
        (user_id, int(call_id)),
    )
    if scored:
        return {"ok": False, "error": "already scored"}
    with transaction() as conn:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM judgment_scores WHERE user_id = %s AND kind = 'call' AND call_id = %s",
                (user_id, int(call_id)),
            )
            c.execute(
                "UPDATE judgment_calls SET status = 'cancelled', cancelled_at = NOW() "
                "WHERE id = %s AND user_id = %s",
                (int(call_id), user_id),
            )
    return {"ok": True}


def _plain(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    for k in (
        "target_price",
        "entry_price",
        "exit_price",
        "raw_pct",
        "bench_pct",
        "excess_pct",
    ):
        if out.get(k) is not None:
            out[k] = float(out[k])
    for k in ("entry_date", "exit_date", "created_at", "cancelled_at"):
        if hasattr(out.get(k), "isoformat"):
            out[k] = out[k].isoformat()
    return out
