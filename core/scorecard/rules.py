"""評分規則（純函式，無 I/O）。

- 交易 → 判斷：買＝看多、賣＝看空；期限 30／90 天。
- 驗證等級：鏈上同步（有 tx_hash）＝verified；記錄日期比成交日晚超過 BACKFILL_DAYS＝backfilled
  （不計分，只列筆數）；其他＝self_reported。
- 評分：raw＝到期價相對進場價的報酬（看空取負），bench＝同區間基準指數同方向報酬，
  excess＝raw−bench，hit＝excess>0。
- 彙總：每組不滿 MIN_SAMPLE 筆不給百分比。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

HORIZONS: tuple[int, ...] = (30, 90)
BACKFILL_DAYS = 3
MIN_SAMPLE = 10
SCORABLE_MARKETS = frozenset(
    {"crypto", "tw_stock", "us_stock", "hk_stock", "jp_stock", "kr_stock", "cn_stock"}
)
# 市場 → 基準（同市場指數 ETF；幣用 BTC）
BENCHMARKS: Dict[str, str] = {
    "tw_stock": "0050.TW",
    "us_stock": "SPY",
    "hk_stock": "2800.HK",
    "jp_stock": "1306.T",
    "kr_stock": "069500.KS",
    "cn_stock": "510300.SS",
    "crypto": "BTC",
}
# 穩定幣本身不是判斷（買 USDC 不叫看多）
STABLECOINS = frozenset(
    {"USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "USDS", "USD₮"}
)
VERIFIED = "verified"
SELF_REPORTED = "self_reported"
BACKFILLED = "backfilled"


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def verification_for(trade: Dict[str, Any]) -> str:
    """鏈上同步＝verified；補記（記錄比成交晚 > BACKFILL_DAYS）＝backfilled；其餘 self_reported。"""
    traded = _as_date(trade.get("traded_at"))
    created = _as_date(trade.get("created_at"))
    if traded and created and (created - traded).days > BACKFILL_DAYS:
        return BACKFILLED
    if (trade.get("source") or "").lower() == "onchain" and trade.get("tx_hash"):
        return VERIFIED
    return SELF_REPORTED


def is_scorable_trade(trade: Dict[str, Any]) -> bool:
    return (
        (trade.get("entry_type") or "trade") == "trade"
        and (trade.get("market") or "") in SCORABLE_MARKETS
        and (trade.get("side") or "") in ("buy", "sell")
        and str(trade.get("symbol") or "").upper() not in STABLECOINS
        and float(trade.get("price") or 0) > 0
        and float(trade.get("quantity") or 0) > 0
        and _as_date(trade.get("traded_at")) is not None
    )


def pending_rows_for(
    trade: Dict[str, Any], horizons: Iterable[int] = HORIZONS
) -> List[Dict[str, Any]]:
    """一筆交易 → 每個期限一列待評分（純函式）。"""
    traded = _as_date(trade["traded_at"])
    verification = verification_for(trade)
    rows = []
    for h in horizons:
        rows.append(
            {
                "entry_id": int(trade["id"]),
                "kind": "trade",
                "symbol": str(trade["symbol"]).upper(),
                "market": trade["market"],
                "side": trade["side"],
                "horizon_days": int(h),
                "entry_price": float(trade["price"]),
                "entry_date": traded,
                "exit_date": traded + timedelta(days=int(h)),
                "verification": verification,
                # 標的就是基準（買 BTC 拿 BTC 當基準）→ 不扣基準，看絕對報酬
                "bench_symbol": (
                    None
                    if BENCHMARKS.get(trade["market"], "").split(".")[0]
                    == str(trade["symbol"]).upper()
                    else BENCHMARKS.get(trade["market"])
                ),
            }
        )
    return rows


@dataclass(frozen=True)
class Score:
    raw_pct: float
    bench_pct: Optional[float]
    excess_pct: float
    hit: bool


def score(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    bench_entry: Optional[float] = None,
    bench_exit: Optional[float] = None,
) -> Score:
    """報酬以百分比表示；看空把符號反過來；沒有基準時 excess＝raw。"""
    sign = 1.0 if side == "buy" else -1.0
    raw = (exit_price / entry_price - 1.0) * sign * 100.0
    bench: Optional[float] = None
    if bench_entry and bench_exit and bench_entry > 0:
        bench = (bench_exit / bench_entry - 1.0) * sign * 100.0
    excess = raw - (bench or 0.0)
    return Score(
        round(raw, 4),
        round(bench, 4) if bench is not None else None,
        round(excess, 4),
        excess > 0,
    )


def summarize(
    rows: Iterable[Dict[str, Any]], *, min_sample: int = MIN_SAMPLE
) -> Dict[str, Any]:
    """已評分列 → 分組彙總（verification × market × horizon）＋主數字。

    每組 n < min_sample 只給 n 與 ``masked=True``（不給百分比）。主數字順序：
    verified/90 → all/90 → all/30，第一個 n ≥ min_sample 的。
    """
    scored = [
        r
        for r in rows
        if r.get("status") == "scored" and r.get("excess_pct") is not None
    ]
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in scored:
        if r.get("verification") == BACKFILLED:
            continue
        key = (r.get("verification"), r.get("market"), int(r.get("horizon_days")))
        groups.setdefault(key, []).append(r)

    def _agg(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(items)
        if n < min_sample:
            return {"n": n, "masked": True, "needed": min_sample - n}
        ex = sorted(float(i["excess_pct"]) for i in items)
        hits = sum(1 for i in items if i.get("hit"))
        mid = n // 2
        median = ex[mid] if n % 2 else (ex[mid - 1] + ex[mid]) / 2
        return {
            "n": n,
            "masked": False,
            "hit_rate": round(hits / n, 4),
            "avg_excess": round(sum(ex) / n, 4),
            "median_excess": round(median, 4),
            "best": round(ex[-1], 4),
            "worst": round(ex[0], 4),
        }

    out_groups = [
        {"verification": k[0], "market": k[1], "horizon_days": k[2], **_agg(v)}
        for k, v in sorted(
            groups.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])
        )
    ]

    def _pool(pred) -> List[Dict[str, Any]]:
        return [r for r in scored if r.get("verification") != BACKFILLED and pred(r)]

    headline: Optional[Dict[str, Any]] = None
    for label, pred in (
        (
            "verified_90",
            lambda r: (
                r.get("verification") == VERIFIED and int(r["horizon_days"]) == 90
            ),
        ),
        ("all_90", lambda r: int(r["horizon_days"]) == 90),
        ("all_30", lambda r: int(r["horizon_days"]) == 30),
    ):
        items = _pool(pred)
        agg = _agg(items)
        if not agg["masked"]:
            headline = {"scope": label, **agg}
            break
    if headline is None:
        all_90 = _pool(lambda r: int(r["horizon_days"]) == 90)
        headline = {"scope": "all_90", **_agg(all_90)}

    counts = {
        "scored": len(scored),
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "unscorable": sum(1 for r in rows if r.get("status") == "unscorable"),
        "backfilled": sum(1 for r in rows if r.get("verification") == BACKFILLED),
        "verified": sum(1 for r in scored if r.get("verification") == VERIFIED),
        # v2 明確喊單（kind=call）：與交易同池計分，這裡只另外數一下
        "calls": sum(1 for r in rows if r.get("kind") == "call"),
    }
    return {
        "headline": headline,
        "groups": out_groups,
        "counts": counts,
        "min_sample": min_sample,
    }


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
