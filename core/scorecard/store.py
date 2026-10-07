"""judgment_scores 資料存取（同步 psycopg；API 端用 run_sync 包）。

寫入一律走 ``DatabaseBase.execute``／``transaction()``——``query_one`` 不 commit
（tests/test_no_writes_via_query_helpers.py）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from core.database.base import DatabaseBase, transaction

from .rules import SCORABLE_MARKETS

MAX_ATTEMPTS = 3


def eligible_trades(user_id: str, *, since_days: int = 120) -> List[Dict[str, Any]]:
    """最近 N 天的合格交易。**含已刪除**：刪掉帳本條目不能讓判斷消失（設計 §2）。"""
    return DatabaseBase.query_all(
        """
        SELECT id, symbol, market, side, entry_type, quantity, price, traded_at, created_at,
               source, chain, tx_hash, deleted_at
        FROM trade_journal
        WHERE user_id = %s AND entry_type = 'trade'
          AND market = ANY(%s)
          AND traded_at >= NOW() - (%s || ' days')::interval
        ORDER BY traded_at ASC
        """,
        (user_id, sorted(SCORABLE_MARKETS), str(int(since_days))),
    )


def existing_entry_ids(user_id: str) -> set:
    rows = DatabaseBase.query_all(
        "SELECT DISTINCT entry_id FROM judgment_scores WHERE user_id = %s AND kind = 'trade'",
        (user_id,),
    )
    return {int(r["entry_id"]) for r in rows}


def insert_pending(user_id: str, rows: List[Dict[str, Any]]) -> int:
    """冪等（UNIQUE + ON CONFLICT DO NOTHING）；回實際插入筆數。"""
    if not rows:
        return 0
    inserted = 0
    with transaction() as conn:
        with conn.cursor() as c:
            for r in rows:
                c.execute(
                    """
                    INSERT INTO judgment_scores
                        (user_id, entry_id, call_id, kind, symbol, market, side, horizon_days,
                         entry_price, entry_date, exit_date, verification, bench_symbol, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (user_id, kind, entry_id, call_id, horizon_days) DO NOTHING
                    """,
                    (
                        user_id,
                        int(r.get("entry_id") or 0),
                        int(r.get("call_id") or 0),
                        r.get("kind", "trade"),
                        r["symbol"],
                        r["market"],
                        r["side"],
                        int(r["horizon_days"]),
                        float(r["entry_price"]),
                        r["entry_date"],
                        r["exit_date"],
                        r["verification"],
                        r.get("bench_symbol"),
                    ),
                )
                inserted += c.rowcount if c.rowcount and c.rowcount > 0 else 0
    return inserted


def due_rows(
    today: date, *, limit: int = 500, user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    params: list = [today]
    where = "status = 'pending' AND exit_date <= %s"
    if user_id:
        where += " AND user_id = %s"
        params.append(user_id)
    params.append(int(limit))
    return DatabaseBase.query_all(
        f"""
        SELECT id, user_id, symbol, market, side, horizon_days, entry_price, entry_date,
               exit_date, bench_symbol, verification, attempts
        FROM judgment_scores
        WHERE {where}
        ORDER BY exit_date ASC, id ASC
        LIMIT %s
        """,
        tuple(params),
    )


def mark_scored(
    row_id: int,
    *,
    exit_price: float,
    raw_pct: float,
    bench_pct: Optional[float],
    excess_pct: float,
    hit: bool,
) -> None:
    DatabaseBase.execute(
        """
        UPDATE judgment_scores
        SET exit_price = %s, raw_pct = %s, bench_pct = %s, excess_pct = %s, hit = %s,
            status = 'scored', scored_at = %s
        WHERE id = %s
        """,
        (
            exit_price,
            raw_pct,
            bench_pct,
            excess_pct,
            bool(hit),
            datetime.now(timezone.utc),
            int(row_id),
        ),
    )


def mark_attempt(row_id: int, attempts: int, note: str) -> None:
    """抓不到價：累計次數，滿 MAX_ATTEMPTS 轉 unscorable。"""
    status = "unscorable" if attempts >= MAX_ATTEMPTS else "pending"
    DatabaseBase.execute(
        "UPDATE judgment_scores SET attempts = %s, status = %s, note = %s WHERE id = %s",
        (int(attempts), status, (note or "")[:200], int(row_id)),
    )


def rows_for_user(user_id: str, *, limit: int = 2000) -> List[Dict[str, Any]]:
    rows = DatabaseBase.query_all(
        """
        SELECT id, entry_id, call_id, kind, symbol, market, side, horizon_days, entry_price, entry_date,
               exit_price, exit_date, raw_pct, bench_symbol, bench_pct, excess_pct, hit,
               verification, status, note, scored_at
        FROM judgment_scores
        WHERE user_id = %s
        ORDER BY exit_date DESC, id DESC
        LIMIT %s
        """,
        (user_id, int(limit)),
    )
    return [_plain(r) for r in rows]


def scored_since(
    user_id: str, since: datetime, *, limit: int = 50
) -> List[Dict[str, Any]]:
    """``since`` 之後評完分的列（早報「🎯 判斷結果」段）。喊單排前面；補記（backfilled）不計分，不列。"""
    rows = DatabaseBase.query_all(
        """
        SELECT kind, symbol, market, side, horizon_days, raw_pct, bench_symbol,
               bench_pct, excess_pct, hit, scored_at
        FROM judgment_scores
        WHERE user_id = %s AND status = 'scored' AND scored_at >= %s
          AND verification <> 'backfilled'
        ORDER BY (kind = 'call') DESC, scored_at DESC, id DESC
        LIMIT %s
        """,
        (user_id, since, int(limit)),
    )
    return [_plain(r) for r in rows]


def _plain(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    for k in ("entry_price", "exit_price", "raw_pct", "bench_pct", "excess_pct"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    for k in ("entry_date", "exit_date"):
        if hasattr(out.get(k), "isoformat"):
            out[k] = out[k].isoformat()
    if hasattr(out.get("scored_at"), "isoformat"):
        out["scored_at"] = out["scored_at"].isoformat()
    return out


def users_with_trades(*, since_days: int = 120) -> List[str]:
    rows = DatabaseBase.query_all(
        """
        SELECT DISTINCT t.user_id
        FROM trade_journal t JOIN users u ON u.user_id = t.user_id
        WHERE u.is_active = TRUE AND t.entry_type = 'trade'
          AND t.market = ANY(%s)
          AND t.traded_at >= NOW() - (%s || ' days')::interval
        """,
        (sorted(SCORABLE_MARKETS), str(int(since_days))),
    )
    return [r["user_id"] for r in rows]
