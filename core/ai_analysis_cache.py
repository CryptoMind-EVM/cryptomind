"""
AI analysis result cache — stores LLM-generated analysis in Redis to avoid
repeated API key charges on page refresh.

Key structure:
  - Cache:     ai:{market}:{symbol}   (TTL 4h)
  - Cooldown:  ai_cd:{market}:{symbol} (TTL 15min)

Graceful degradation:
  If Redis is unavailable, all cache operations return None / no-op
  and the system falls back to calling LLM every time (current behavior).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.redis_url import resolve_redis_url

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────
_CACHE_TTL = 4 * 60 * 60  # 4 hours
_COOLDOWN_TTL = 15 * 60  # 15 minutes
_KEY_PREFIX = "ai:"
_COOLDOWN_PREFIX = "ai_cd:"

# ── Module-level state ────────────────────────────────────────────────────────
_redis: Optional[Any] = None
_redis_checked: bool = False


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _get_redis() -> Optional[Any]:
    """Return a live async Redis client, or None if unavailable."""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis

    _redis_checked = True
    redis_url, source = resolve_redis_url()
    if not redis_url:
        logger.info("[AIAnalysisCache] No Redis configured — caching disabled")
        return None

    try:
        import redis.asyncio as aioredis  # noqa: PLC0415

        client = aioredis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis = client
        logger.info("[AIAnalysisCache] Redis connected via %s", source)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[AIAnalysisCache] Redis unavailable: %s", exc)
        _redis = None

    return _redis


def _cache_key(market: str, symbol: str, user_id: str) -> str:
    return f"{_KEY_PREFIX}{market}:{symbol}:{user_id}"


def _cooldown_key(market: str, symbol: str, user_id: str) -> str:
    return f"{_COOLDOWN_PREFIX}{market}:{symbol}:{user_id}"


# ── Public API ────────────────────────────────────────────────────────────────


async def get_cached_analysis(market: str, symbol: str, user_id: str) -> Optional[dict]:
    """Return cached AI analysis result, or None on miss / Redis unavailable."""
    import orjson  # noqa: PLC0415

    r = await _get_redis()
    if not r:
        return None

    try:
        raw = await r.get(_cache_key(market, symbol, user_id))
        if raw is not None:
            return orjson.loads(raw)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[AIAnalysisCache] get failed: %s", exc)
    return None


async def cache_analysis(
    market: str,
    symbol: str,
    user_id: str,
    result: dict,
) -> None:
    """Store AI analysis result in Redis with 4h TTL."""
    import orjson  # noqa: PLC0415

    r = await _get_redis()
    if not r:
        return

    now = datetime.now(timezone.utc)
    payload = {
        **result,
        "cached_at": now.isoformat(),
        "cache_expires_at": datetime.fromtimestamp(
            now.timestamp() + _CACHE_TTL, tz=timezone.utc
        ).isoformat(),
    }

    try:
        await r.setex(
            _cache_key(market, symbol, user_id), _CACHE_TTL, orjson.dumps(payload)
        )
        logger.info(
            "[AIAnalysisCache] Cached %s:%s:%s for %ds",
            market,
            symbol,
            user_id,
            _CACHE_TTL,
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[AIAnalysisCache] set failed: %s", exc)


async def check_cooldown(market: str, symbol: str, user_id: str) -> Optional[float]:
    """Return remaining cooldown seconds, or None if no cooldown is active."""
    r = await _get_redis()
    if not r:
        return None

    try:
        ttl = await r.ttl(_cooldown_key(market, symbol, user_id))
        if ttl and ttl > 0:
            return float(ttl)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[AIAnalysisCache] cooldown check failed: %s", exc)
    return None


async def set_cooldown(market: str, symbol: str, user_id: str) -> None:
    """Set a 15min cooldown to prevent accidental repeated LLM calls."""
    r = await _get_redis()
    if not r:
        return

    try:
        await r.setex(_cooldown_key(market, symbol, user_id), _COOLDOWN_TTL, b"1")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[AIAnalysisCache] cooldown set failed: %s", exc)


async def reset_connection() -> None:
    """Force reconnection to Redis (useful after network recovery)."""
    global _redis, _redis_checked
    _redis = None
    _redis_checked = False
    await _get_redis()
