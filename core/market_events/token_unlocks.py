"""代幣解鎖排程——DefiLlama 公開資料集（免金鑰）。

``api.llama.fi/emissions`` 2026-09 起改付費（402），但 ``defillama-datasets.llama.fi/
emissionsIndex`` 仍是公開 S3 檔（約 22 MB、370 個代幣）。每筆有 ``unlockEvents``
（timestamp、各分配的數量與類別）、``circSupply``、``tokenPrice[0].symbol``。

太大不能每題抓：這裡壓成 ``{SYMBOL: {slug, name, circ, price, events[]}}`` 進共用快取
（Redis＋DB，12 小時），行事曆 cron 與 ``get_token_unlocks`` 工具都讀同一份。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

INDEX_URL = "https://defillama-datasets.llama.fi/emissionsIndex"
CACHE_KEY = "market_events:unlocks:v1"
CACHE_TTL_SECONDS = 12 * 3600
FETCH_TIMEOUT_SECONDS = 90
# 壓縮時只留這個視窗內的事件（過去 1 天讓「今天解鎖」還看得到）
KEEP_PAST_DAYS = 1
KEEP_FUTURE_DAYS = 400
# 行事曆門檻：單日解鎖 ≥ 流通量的這個比例才提醒（小額線性釋放天天有，沒意義）
CALENDAR_MIN_PCT_OF_CIRC = 0.1
CALENDAR_HORIZON_DAYS = 30


def compact_index(
    raw: Dict[str, Any], *, now_ts: Optional[float] = None
) -> Dict[str, Dict[str, Any]]:
    """把 emissionsIndex 原始 JSON 壓成 symbol → 摘要（純函式）。

    同 symbol 多個協議（例如 wrapped 版本）取流通量大的那個；只留 cliff 事件
    （線性釋放每天都在發，不是「事件」）。
    """
    now_ts = time.time() if now_ts is None else now_ts
    lo = now_ts - KEEP_PAST_DAYS * 86400
    hi = now_ts + KEEP_FUTURE_DAYS * 86400
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw.get("data") or []:
        price_row = (item.get("tokenPrice") or [{}])[0] or {}
        symbol = str(price_row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        circ = float(item.get("circSupply") or 0)
        events: List[Dict[str, Any]] = []
        for ue in item.get("unlockEvents") or []:
            ts = int(ue.get("timestamp") or 0)
            if not ts or ts < lo or ts > hi:
                continue
            allocs = ue.get("cliffAllocations") or []
            amount = sum(float(a.get("amount") or 0) for a in allocs)
            if amount <= 0:
                continue
            events.append(
                {
                    "ts": ts,
                    "amount": amount,
                    "recipients": [str(a.get("recipient") or "") for a in allocs][:4],
                    "categories": sorted(
                        {str(a.get("category")) for a in allocs if a.get("category")}
                    ),
                }
            )
        entry = {
            "slug": item.get("protocolSlug"),
            "name": item.get("name"),
            "circ": circ,
            "price": float(price_row.get("price") or 0),
            "events": sorted(events, key=lambda e: e["ts"]),
        }
        if symbol in out and out[symbol]["circ"] >= circ:
            continue
        out[symbol] = entry
    return out


def _download_index() -> Dict[str, Any]:
    resp = httpx.get(INDEX_URL, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def get_unlock_index(*, force: bool = False) -> Dict[str, Dict[str, Any]]:
    """快取優先；沒有就抓一次壓縮後存。抓失敗回空 dict（呼叫端當「查無資料」）。"""
    from core.database.cache import get_cache, set_cache

    if not force:
        try:
            cached = get_cache(CACHE_KEY)
        except Exception as exc:  # noqa: BLE001 — 快取壞了就重抓
            logger.debug("[unlocks] cache read failed: %s", exc)
            cached = None
        if isinstance(cached, dict) and cached.get("index"):
            return cached["index"]
    try:
        raw = _download_index()
    except Exception as exc:  # noqa: BLE001 — 資料源掛了不影響其他事件
        logger.warning("[unlocks] index download failed: %s", type(exc).__name__)
        return {}
    index = compact_index(raw)
    try:
        set_cache(
            CACHE_KEY,
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "index": index},
            ttl=CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[unlocks] cache write failed: %s", exc)
    return index


def upcoming_unlocks(
    symbol: str,
    *,
    today: date,
    days: int = 90,
    index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """某代幣未來 ``days`` 天的解鎖（同一天多筆分配合併成一筆）。"""
    index = get_unlock_index() if index is None else index
    entry = index.get(symbol.upper())
    if not entry:
        return []
    circ = float(entry.get("circ") or 0)
    by_day: Dict[date, Dict[str, Any]] = {}
    end = today + timedelta(days=days)
    for ev in entry.get("events") or []:
        d = datetime.fromtimestamp(int(ev["ts"]), tz=timezone.utc).date()
        if d < today or d > end:
            continue
        slot = by_day.setdefault(
            d, {"date": d, "amount": 0.0, "recipients": [], "categories": set()}
        )
        slot["amount"] += float(ev.get("amount") or 0)
        slot["recipients"].extend(r for r in ev.get("recipients") or [] if r)
        slot["categories"].update(ev.get("categories") or [])
    result = []
    for d in sorted(by_day):
        slot = by_day[d]
        pct = (slot["amount"] / circ * 100) if circ > 0 else None
        result.append(
            {
                "date": d,
                "amount": slot["amount"],
                "pct_of_circ": round(pct, 2) if pct is not None else None,
                "usd": slot["amount"] * float(entry.get("price") or 0) or None,
                "recipients": list(dict.fromkeys(slot["recipients"]))[:4],
                "categories": sorted(slot["categories"]),
            }
        )
    return result


def format_amount(amount: float, language: str = "zh-TW") -> str:
    """數量人類化：中文用萬／億，其他用 K／M／B。"""
    lang = (language or "").lower()
    if lang.startswith("zh"):
        if amount >= 1e8:
            return f"{amount / 1e8:.2f}億"
        if amount >= 1e4:
            return f"{amount / 1e4:.0f}萬"
        return f"{amount:,.0f}"
    if amount >= 1e9:
        return f"{amount / 1e9:.2f}B"
    if amount >= 1e6:
        return f"{amount / 1e6:.1f}M"
    if amount >= 1e3:
        return f"{amount / 1e3:.0f}K"
    return f"{amount:,.0f}"
