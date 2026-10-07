"""RunMetrics 滾動存放（admin 面板用；tier 3 觀測項）。

``log_run_metrics`` 每題印一行 log——那是 log pipeline 用的；這裡多留一份最近 N 筆
在 Redis（API／worker 共用），admin 面板才能不靠 log 看 route 命中率、延遲分佈、
工具使用、phased／router 狀態。沒 Redis 就退回 in-process deque（單 worker 自己看）。

寫入永不丟例外（指標壞掉不能影響主流程），讀取端做聚合。
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from collections import Counter, deque
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

REDIS_KEY = "agent:runmetrics:v1"
MAX_ROWS = 5000
_FALLBACK: deque = deque(maxlen=2000)

_FIELDS = (
    "route",
    "elapsed_s",
    "ttft_s",
    "tool_calls",
    "response_chars",
    "model",
    "provider",
    "preset_id",
    "agents",
    "prompt_tokens",
    "completion_tokens",
    "depth",
    "domains",
    "gate",
    "source",
    "reason",
    "skills",
)


def _redis():
    try:
        from core.database.cache import get_redis_client

        return get_redis_client()
    except Exception:  # noqa: BLE001
        return None


def record(fields: Dict[str, Any]) -> None:
    """存一筆（只留已知欄位＋ts）。任何失敗吞掉。"""
    try:
        row = {k: fields.get(k) for k in _FIELDS}
        row["ts"] = time.time()
        payload = json.dumps(row, ensure_ascii=False, default=str)
        client = _redis()
        if client is not None:
            pipe = client.pipeline()
            pipe.lpush(REDIS_KEY, payload)
            pipe.ltrim(REDIS_KEY, 0, MAX_ROWS - 1)
            pipe.execute()
        else:
            _FALLBACK.appendleft(row)
    except Exception as exc:  # noqa: BLE001 — 指標壞掉不能影響主流程
        logger.debug("[RunMetricsStore] record skipped: %s", exc)


def _load(limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    client = _redis()
    if client is not None:
        try:
            raw = client.lrange(REDIS_KEY, 0, limit - 1)
            out = []
            for item in raw:
                try:
                    out.append(json.loads(item))
                except (TypeError, ValueError):
                    continue
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunMetricsStore] redis read failed: %s", exc)
    return list(_FALLBACK)[:limit]


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(
    rows: Iterable[Dict[str, Any]], *, hours: float = 24.0, now: Optional[float] = None
) -> Dict[str, Any]:
    """純函式：最近 hours 小時的聚合。"""
    now = now or time.time()
    cutoff = now - hours * 3600
    recent = [r for r in rows if _num(r.get("ts")) and float(r["ts"]) >= cutoff]
    routes = Counter(str(r.get("route") or "-") for r in recent)
    sources = Counter(str(r.get("source") or "-") for r in recent)
    depths = Counter(str(r.get("depth") or "-") for r in recent)
    providers = Counter(str(r.get("provider") or "-") for r in recent)
    models = Counter(str(r.get("model") or "-") for r in recent)
    skills: Counter = Counter()
    for r in recent:
        for s in str(r.get("skills") or "").split(","):
            if s and s != "-":
                skills[s] += 1
    latency_by_route: Dict[str, Dict[str, Optional[float]]] = {}
    for route in routes:
        vals = [
            _num(r.get("elapsed_s"))
            for r in recent
            if str(r.get("route") or "-") == route
        ]
        vals = [v for v in vals if v is not None]
        latency_by_route[route] = {
            "n": len(vals),
            "p50": _pct(vals, 0.5),
            "p95": _pct(vals, 0.95),
            "mean": round(statistics.fmean(vals), 2) if vals else None,
        }
    tool_calls = [_num(r.get("tool_calls")) for r in recent]
    tool_calls = [v for v in tool_calls if v is not None]
    prompt_tokens = sum(int(_num(r.get("prompt_tokens")) or 0) for r in recent)
    completion_tokens = sum(int(_num(r.get("completion_tokens")) or 0) for r in recent)
    fast = sum(
        v for k, v in routes.items() if k.startswith("fast_path") or k == "cache_hit"
    )
    return {
        "hours": hours,
        "runs": len(recent),
        "fast_path_rate": round(fast / len(recent), 3) if recent else None,
        "routes": dict(routes.most_common()),
        "sources": dict(sources.most_common()),
        "depths": dict(depths.most_common()),
        "providers": dict(providers.most_common(5)),
        "models": dict(models.most_common(5)),
        "latency_by_route": latency_by_route,
        "tool_calls_mean": round(statistics.fmean(tool_calls), 2)
        if tool_calls
        else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "top_skills": dict(skills.most_common(8)),
        "fallback_reasons": dict(
            Counter(
                str(r.get("reason"))
                for r in recent
                if r.get("reason") and r.get("reason") != "-"
            ).most_common(5)
        ),
    }


def summary(hours: float = 24.0) -> Dict[str, Any]:
    return summarize(_load(), hours=hours)
