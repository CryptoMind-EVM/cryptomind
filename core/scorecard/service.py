"""建檔（交易 → 待評分列）與到期評分。cron 與手動同步都走這裡。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Dict, Optional

from . import prices, rules, store

logger = logging.getLogger(__name__)

CloseLookup = Callable[[str, str, date], Optional[float]]


def build_pending_for_user(user_id: str, *, since_days: int = 120) -> int:
    """掃使用者近期交易，沒有分數列的建 pending（每個期限一列）。回新建筆數。"""
    trades = [
        t
        for t in store.eligible_trades(user_id, since_days=since_days)
        if rules.is_scorable_trade(t)
    ]
    if not trades:
        return 0
    known = store.existing_entry_ids(user_id)
    rows = []
    for t in trades:
        if int(t["id"]) in known:
            continue
        rows.extend(rules.pending_rows_for(t))
    return store.insert_pending(user_id, rows)


def score_row(
    row: Dict[str, Any], *, close_lookup: CloseLookup = prices.close_on
) -> str:
    """一列：抓到期價與基準 → scored；抓不到累計 attempts。回最終狀態。"""
    exit_date = row["exit_date"]
    exit_price = close_lookup(row["symbol"], row["market"], exit_date)
    if not exit_price:
        attempts = int(row.get("attempts") or 0) + 1
        store.mark_attempt(
            int(row["id"]), attempts, f"no close for {row['symbol']} on {exit_date}"
        )
        return "unscorable" if attempts >= store.MAX_ATTEMPTS else "pending"
    bench_entry = bench_exit = None
    bench = row.get("bench_symbol")
    if bench:
        bench_entry = close_lookup(bench, row["market"], row["entry_date"])
        bench_exit = close_lookup(bench, row["market"], exit_date)
    s = rules.score(
        side=row["side"],
        entry_price=float(row["entry_price"]),
        exit_price=float(exit_price),
        bench_entry=bench_entry,
        bench_exit=bench_exit,
    )
    store.mark_scored(
        int(row["id"]),
        exit_price=float(exit_price),
        raw_pct=s.raw_pct,
        bench_pct=s.bench_pct,
        excess_pct=s.excess_pct,
        hit=s.hit,
    )
    return "scored"


def score_due(
    today: date, *, close_lookup: CloseLookup = prices.close_on, limit: int = 500
) -> Dict[str, int]:
    summary = {"scored": 0, "pending": 0, "unscorable": 0, "errors": 0}
    for row in store.due_rows(today, limit=limit):
        try:
            summary[score_row(row, close_lookup=close_lookup)] += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 — 單列失敗不中斷
            logger.warning("[scorecard] score row %s failed: %s", row.get("id"), exc)
            summary["errors"] += 1
    return summary


def score_due_for_user(
    user_id: str,
    today: date,
    *,
    close_lookup: CloseLookup = prices.close_on,
    limit: int = 200,
) -> Dict[str, int]:
    """只評這個人的到期列（手動刷新用）。"""
    summary = {"scored": 0, "pending": 0, "unscorable": 0, "errors": 0}
    for row in store.due_rows(today, limit=limit, user_id=user_id):
        try:
            summary[score_row(row, close_lookup=close_lookup)] += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scorecard] score row %s failed: %s", row.get("id"), exc)
            summary["errors"] += 1
    return summary


def scorecard_for_user(user_id: str) -> Dict[str, Any]:
    rows = store.rows_for_user(user_id)
    summary = rules.summarize(rows)
    recent = [r for r in rows if r["status"] == "scored"][:20]
    return {**summary, "recent": recent}
