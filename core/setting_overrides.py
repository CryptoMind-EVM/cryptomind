"""後台覆寫（2026-09-27 DANNY：開關與額度要能在後台切，不用 SSH 改 env 再重啟）。

值的優先順序：**後台覆寫 > 環境變數 > 程式預設**。清掉覆寫就回到環境變數。

- 正本：DB ``admin_setting_overrides``（c055）；每次改動寫 ``config_audit_log``
  （config_key = ``override:<KEY>``，後台設定頁的 Recent Changes 看得到）。
- 發佈：整份 dict 存 Redis ``settings:overrides``；各服務（api、analysis-worker、cron）
  讀取時最多每 ``_TTL`` 秒重讀一次 Redis，所以改完約 15 秒內生效，不用重啟。
- 保底：api 每 60 秒把 DB 再發佈一次（Redis 重啟、key 被清掉時自動補回）。
- 讀取絕不丟例外：Redis 掛了就沿用上一份；從沒讀到過就當沒有覆寫（＝環境變數）。

**哪些可以覆寫**只看下面兩個清單。安全類（PII 遮蔽、登入鎖定、consent guard）刻意不放：
後台帳號被盜也不能一鍵關掉保護；``core/config.py`` 在 import 時就定型的旗標（錢包監測、
信任分數、多鏈等）也不放——切了要重啟才生效，放進來只會讓人以為改了有用。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 這些旗標的 getter 都是「每次呼叫才讀 env_flag」，覆寫即時生效
# （tests/test_admin_setting_overrides.py 檢查它們不是 core/config.py 的 import 時常數）。
OVERRIDABLE_FLAGS = frozenset(
    {
        "CONVERSATION_SHARE_ENABLED",
        "CRYPTO_TAB_ENABLED",
        "EMAIL_BRIEF_ENABLED",
        "DAILY_BRIEF_ENABLED",
        "REOWN_SOCIAL_LOGIN_ENABLED",
        "MODERATION_ENABLED",
        "VISION_ENABLED",
        "VISION_STORAGE_ENABLED",
        "GUEST_AGENT_ENABLED",
        "BASE_APP_NOTIFICATIONS_ENABLED",
        "PEOPLE_PROJECTS_DISCOVER_ENABLED",
        "DISCOVER_OC_ENABLED",
        "DISCOVER_AI_REVIEW_ENABLED",
        "PROPOSAL_STUDIO_ENABLED",
    }
)

# 數值參數：名稱 -> (最小, 最大)
OVERRIDABLE_PARAMS = {
    "FREE_DAILY_CHAT_LIMIT": (0, 10_000),
    "GUEST_DAILY_QUESTIONS": (0, 1_000),
    "GUEST_GLOBAL_DAILY_CAP": (0, 1_000_000),
    "BYOK_FALLBACK_DAILY_LIMIT": (0, 1_000_000),
}

REDIS_KEY = "settings:overrides"
_REDIS_TTL = 7 * 86400  # api 每 60 秒重新發佈；TTL 只是防止孤兒 key
_TTL = 15.0

_cache: dict = {"data": {}, "at": 0.0, "loaded": False}


class InvalidOverride(ValueError):
    pass


def is_overridable(key: str) -> bool:
    return key in OVERRIDABLE_FLAGS or key in OVERRIDABLE_PARAMS


def _refresh_from_cache() -> None:
    _cache["at"] = time.monotonic()
    try:
        from core.shared_cache import get_json

        data = get_json(REDIS_KEY)
    except Exception as exc:  # noqa: BLE001 — 讀不到就沿用上一份
        logger.debug("[overrides] read failed: %s", exc)
        return
    if isinstance(data, dict):
        _cache["data"] = {str(k): str(v) for k, v in data.items()}
        _cache["loaded"] = True
    elif data is None:
        # key 不存在：沒有任何覆寫（或 Redis 剛重啟、api 還沒重新發佈）
        _cache["data"] = {}
        _cache["loaded"] = True


def get(key: str) -> Optional[str]:
    """覆寫值（字串）；沒有覆寫回 None。只查白名單裡的 key。"""
    if not is_overridable(key):
        return None
    if time.monotonic() - _cache["at"] > _TTL:
        _refresh_from_cache()
    return _cache["data"].get(key)


def param_raw(name: str) -> str:
    """數值參數的原始字串：後台覆寫 > 環境變數（都沒有回 ""，由呼叫端套自己的預設）。"""
    import os

    try:
        value = get(name)
    except Exception:  # noqa: BLE001
        value = None
    return value if value is not None else os.getenv(name, "").strip()


def int_param(name: str, fallback: int) -> int:
    """後台覆寫的整數；沒有覆寫（或壞值）回 fallback（通常是 import 時讀好的 env 值）。"""
    try:
        value = get(name)
    except Exception:  # noqa: BLE001
        value = None
    if value is None:
        return fallback
    try:
        return max(0, int(value))
    except ValueError:
        return fallback


def get_all() -> dict:
    if time.monotonic() - _cache["at"] > _TTL:
        _refresh_from_cache()
    return dict(_cache["data"])


def invalidate_local() -> None:
    """下一次 get() 立刻重讀（本 process 剛改完時用）。"""
    _cache["at"] = 0.0


def validate(key: str, value) -> str:
    """回傳正規化後的字串；不合法丟 InvalidOverride。"""
    raw = str(value).strip().lower() if value is not None else ""
    if key in OVERRIDABLE_FLAGS:
        if raw in ("true", "1", "yes", "on"):
            return "true"
        if raw in ("false", "0", "no", "off"):
            return "false"
        raise InvalidOverride("value must be true or false")
    if key in OVERRIDABLE_PARAMS:
        lo, hi = OVERRIDABLE_PARAMS[key]
        try:
            n = int(raw)
        except ValueError:
            raise InvalidOverride("value must be an integer") from None
        if not lo <= n <= hi:
            raise InvalidOverride(f"value must be between {lo} and {hi}")
        return str(n)
    raise InvalidOverride("this setting cannot be changed from the admin panel")


# ── DB（同步 psycopg；API 端用 run_sync 包）──────────────────────────────


def load_rows() -> dict:
    """{key: {"value", "updated_by", "updated_at"}}（只收白名單裡的 key）。"""
    from core.database.base import DatabaseBase

    rows = DatabaseBase.query_all(
        "SELECT key, value, updated_by, updated_at FROM admin_setting_overrides"
    )
    out = {}
    for r in rows:
        if is_overridable(r["key"]):
            at = r.get("updated_at")
            out[r["key"]] = {
                "value": r["value"],
                "updated_by": r.get("updated_by"),
                "updated_at": at.isoformat() if hasattr(at, "isoformat") else at,
            }
    return out


def publish() -> dict:
    """DB → Redis（整份覆蓋）。回傳發佈出去的 {key: value}。"""
    data = {k: v["value"] for k, v in load_rows().items()}
    from core.shared_cache import set_json

    set_json(REDIS_KEY, data, ttl=_REDIS_TTL)
    _cache.update({"data": dict(data), "at": time.monotonic(), "loaded": True})
    return data


def _audit(c, key: str, old: Optional[str], new: Optional[str], by: str) -> None:
    c.execute(
        "INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by, changed_at) "
        "VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)",
        (f"override:{key}", old, new, by),
    )


def set_override(key: str, value, changed_by: str) -> str:
    normalized = validate(key, value)
    from core.database.base import transaction

    with transaction() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM admin_setting_overrides WHERE key = %s", (key,))
        row = c.fetchone()
        old = row[0] if row else None
        c.execute(
            "INSERT INTO admin_setting_overrides (key, value, updated_by, updated_at) "
            "VALUES (%s, %s, %s, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = NOW()",
            (key, normalized, changed_by),
        )
        _audit(c, key, old, normalized, changed_by)
    publish()
    logger.info("[overrides] %s=%s by %s (was %s)", key, normalized, changed_by, old)
    return normalized


def clear_override(key: str, changed_by: str) -> bool:
    if not is_overridable(key):
        raise InvalidOverride("this setting cannot be changed from the admin panel")
    from core.database.base import transaction

    with transaction() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM admin_setting_overrides WHERE key = %s RETURNING value", (key,)
        )
        row = c.fetchone()
        if row:
            _audit(c, key, row[0], None, changed_by)
    publish()
    logger.info("[overrides] %s cleared by %s", key, changed_by)
    return bool(row)


async def republish_task(interval: float = 60.0) -> None:
    """api 常駐：定期 DB → Redis，並更新本服務的旗標快照（設定中心比對用）。"""
    import asyncio

    from api.utils import run_sync
    from core.feature_flags import store_service_snapshot

    while True:
        try:
            await run_sync(publish)
            await run_sync(store_service_snapshot, "api")
        except Exception as exc:  # noqa: BLE001 — DB 還沒好就下一輪再試
            logger.debug("[overrides] republish skipped: %s", exc)
        await asyncio.sleep(interval)
