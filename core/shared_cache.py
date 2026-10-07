"""
shared_cache.py — 跨 process / 跨 instance 的共用快取（Redis L2）。

設計目的：
    多 worker / 多 instance 部署時，in-process 快取各自為政，每個 worker
    都會獨立打上游（YFinance / CoinGecko ...），造成被限速或額度爆炸。
    本模組提供一層共用 Redis 快取，讓「每檔標的每個 TTL 只打上游一次」。

設計原則（對齊 core/ai_analysis_cache.py）：
    1. 全 sync 介面 —— 給 base_provider 這種 sync / thread-pool 路徑使用。
    2. lazy client，第一次用才連線；連不上只檢查一次。
    3. 優雅降級 —— Redis 不可用時所有操作變 no-op，呼叫端自動 fallback
       到 L1 in-process 快取 + 上游抓取（即現行行為），不會壞。
    4. 序列化用 orjson（與 ai_analysis_cache 一致）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from core.redis_url import resolve_redis_url

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────
_client: Optional[Any] = None
_checked: bool = False


def _get_client() -> Optional[Any]:
    """回傳可用的 sync Redis client，不可用則回 None（只檢查一次）。"""
    global _client, _checked
    if _checked:
        return _client

    _checked = True
    redis_url, source = resolve_redis_url()
    # 沒設定，或只有 in-memory fallback（rate limiter / CI 用）→ 不啟用 L2
    if not redis_url or redis_url.startswith("memory://"):
        logger.info("[SharedCache] No external Redis configured — L2 disabled")
        return None

    try:
        import redis as _redis  # noqa: PLC0415

        client = _redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _client = client
        logger.info("[SharedCache] Redis connected via %s", source)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[SharedCache] Redis unavailable: %s — L2 disabled", exc)
        _client = None

    return _client


def reset() -> None:
    """重置連線狀態（主要給測試用）。"""
    global _client, _checked
    _client = None
    _checked = False


# ── Public API ────────────────────────────────────────────────────────────────


def get_json(key: str) -> Optional[Any]:
    """讀取共用快取；miss 或 Redis 不可用時回 None。"""
    client = _get_client()
    if not client:
        return None

    try:
        import orjson  # noqa: PLC0415

        raw = client.get(key)
        if raw is not None:
            return orjson.loads(raw)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[SharedCache] get(%s) failed: %s", key, exc)
    return None


def set_json(key: str, value: Any, ttl: int) -> None:
    """寫入共用快取並設定 TTL（秒）；Redis 不可用時為 no-op。"""
    client = _get_client()
    if not client:
        return

    try:
        import orjson  # noqa: PLC0415

        client.setex(key, ttl, orjson.dumps(value))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[SharedCache] set(%s) failed: %s", key, exc)
