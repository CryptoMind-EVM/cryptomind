"""
系統快取 — Redis-first with DB fallback

Read path:  Redis → DB fallback
Write path: Redis (async best-effort) + DB (always)
Delete:     Redis + DB

When Redis is unavailable every operation silently degrades to DB-only,
so existing callers are never disrupted.
"""

import asyncio
import json
import logging
from typing import Any, Optional

from .base import DatabaseBase

logger = logging.getLogger(__name__)

# ── Redis connection ────────────────────────────────────────────────────────────
_KEY_PREFIX = "stock_agent:"
_redis_client = None
_redis_available = False


def _init_redis() -> None:
    """Try to connect to Redis; set ``_redis_available`` flag."""
    global _redis_client, _redis_available
    if _redis_available:
        return  # already connected

    try:
        import redis as redis_lib
    except ImportError:
        logger.debug("redis package not installed — DB-only cache")
        return

    # Reuse the project-wide URL resolver
    from core.redis_url import redact_redis_url, resolve_redis_url

    redis_url, source = resolve_redis_url()
    # memory://＝開發／測試明確不用 Redis（shared_cache、analysis_queue 同規則）。
    # 以前這裡照樣拿去 from_url → ValueError，每次呼叫都重試並印一行 WARNING。
    if not redis_url or redis_url.startswith("memory://"):
        logger.debug("REDIS_URL not set — DB-only cache")
        return

    try:
        _redis_client = redis_lib.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        _redis_client.ping()
        _redis_available = True
        logger.info(
            "Cache Redis connected (%s): %s", source, redact_redis_url(redis_url)
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("Cache Redis connection failed, using DB fallback: %s", exc)
        _redis_client = None
        _redis_available = False


def get_redis_client():
    """共用的同步 Redis client；沒設定或連不上回 None（呼叫端自備 fallback）。

    給快取以外的「跨 worker 一致性」需求用——例如 SIWE nonce 的用後即焚
    （2026-09-12）：in-memory 集合每個 gunicorn worker 各一份，擋不住重放。
    """
    _init_redis()
    return _redis_client if _redis_available else None


async def warmup_redis_async() -> None:
    """在 lifespan 內提前觸發 Redis connect（避免第一波 request 卡 30-64 秒）。

    背景：``_init_redis`` 內含同步 ``redis.from_url(...).ping()``，最壞 4 秒。
    過去是 lazy（第一個 ``get_cache`` call 才觸發），導致 cold-start 後第一波
    user request 各自等 Redis 連線，response time 累積到 30-64 秒。

    本函數把 connect 推到 ``_db_executor`` thread pool 內跑，啟動時就完成。
    """
    from api.utils import run_sync

    await run_sync(_init_redis)


def _redis_key(key: str) -> str:
    """Namespace a bare cache key for Redis."""
    return f"{_KEY_PREFIX}{key}"


# ── Internal Redis helpers (never raise) ────────────────────────────────────────


def _redis_get(key: str) -> Optional[Any]:
    if not _redis_available:
        return None
    try:
        val = _redis_client.get(_redis_key(key))
        if val is not None:
            return json.loads(val)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("Redis GET failed for %s: %s", key, exc)
    return None


def _redis_set(key: str, data: Any, ttl: Optional[int] = None) -> bool:
    if not _redis_available:
        return False
    try:
        val = json.dumps(data, ensure_ascii=False)
        rk = _redis_key(key)
        if ttl is not None and ttl > 0:
            _redis_client.setex(rk, ttl, val)
        else:
            _redis_client.set(rk, val)
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("Redis SET failed for %s: %s", key, exc)
        return False


def _redis_delete(key: str) -> bool:
    if not _redis_available:
        return False
    try:
        return bool(_redis_client.delete(_redis_key(key)))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("Redis DEL failed for %s: %s", key, exc)
        return False


def _redis_clear() -> bool:
    if not _redis_available:
        return False
    try:
        # Use SCAN to avoid blocking on large keyspaces
        cursor = 0
        while True:
            cursor, keys = _redis_client.scan(
                cursor, match=f"{_KEY_PREFIX}*", count=100
            )
            if keys:
                _redis_client.delete(*keys)
            if cursor == 0:
                break
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("Redis SCAN/DEL failed: %s", exc)
        return False


# ── Internal DB helpers (original implementation) ───────────────────────────────


def _db_set(key: str, data: Any) -> None:
    """Write cache entry to system_cache table (upsert)."""
    json_str = json.dumps(data, ensure_ascii=False)
    DatabaseBase.execute(
        """
        INSERT INTO system_cache (key, value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT(key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
    """,
        (key, json_str),
    )


def _db_get(key: str) -> Optional[Any]:
    """Read cache entry from system_cache table."""
    result = DatabaseBase.query_one(
        "SELECT value FROM system_cache WHERE key = %s",
        (key,),
    )
    if result:
        try:
            return json.loads(result["value"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("DB cache read error for %s: %s", key, exc)
            return None
    return None


def _db_delete(key: str) -> bool:
    """Delete cache entry from system_cache table."""
    rows_affected = DatabaseBase.execute(
        "DELETE FROM system_cache WHERE key = %s",
        (key,),
    )
    return rows_affected > 0


def _db_clear() -> None:
    """Delete all entries from system_cache table."""
    DatabaseBase.execute("DELETE FROM system_cache")


# ── Public API (backward-compatible) ───────────────────────────────────────────


def set_cache(key: str, data: Any, ttl: Optional[int] = None) -> None:
    """
    Store *data* under *key*.

    Writes to Redis (with optional TTL) **and** DB (always).
    The DB write acts as the persistent fallback so data survives
    Redis restarts.
    """
    _init_redis()
    _redis_set(key, data, ttl=ttl)
    # DB is always written for durability
    _db_set(key, data)


def get_cache(key: str) -> Optional[Any]:
    """
    Retrieve cached data for *key*.

    Tries Redis first; falls back to DB on miss or Redis failure.
    """
    _init_redis()
    result = _redis_get(key)
    if result is not None:
        return result
    # Fallback to DB
    return _db_get(key)


def delete_cache(key: str) -> bool:
    """
    Remove *key* from both Redis and DB.

    Returns ``True`` if the key existed in at least one store.
    """
    _init_redis()
    redis_ok = _redis_delete(key)
    db_ok = _db_delete(key)
    return redis_ok or db_ok


def consume_cache(key: str) -> bool:
    """一次性取用：刪掉 *key*，回傳「這次是不是我刪到的」。

    以 DB 的刪除筆數為準——set_cache 一定寫 DB，而 Postgres 對同一列的 DELETE 互斥，
    兩個請求同時搶只有一個拿到 True（delete_cache 的 ``redis_ok or db_ok`` 在交錯時
    兩邊都可能回 True，不能拿來當一次性判斷）。Redis 那份盡力刪。
    """
    _init_redis()
    _redis_delete(key)
    return _db_delete(key)


def purge_cache_prefix(prefix: str, older_than_seconds: int) -> int:
    """刪掉 DB 裡 key 以 *prefix* 開頭、超過 N 秒沒更新的列，回傳刪除筆數。

    DB 那層不吃 TTL（Redis 會自己過期），短命的一次性資料（如 Telegram 綁定碼）
    要靠這個收掉。LIKE 的 ``_``／``%`` 先跳脫，只比對真正的前綴。
    """
    pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return DatabaseBase.execute(
        "DELETE FROM system_cache WHERE key LIKE %s "
        "AND updated_at < NOW() - make_interval(secs => %s)",
        (pattern, int(older_than_seconds)),
    )


def clear_all_cache() -> None:
    """
    Remove **all** cache entries from both Redis and DB.

    Redis keys are scoped to the ``stock_agent:`` prefix so
    other tenants sharing the same Redis instance are unaffected.
    """
    _init_redis()
    _redis_clear()
    _db_clear()
