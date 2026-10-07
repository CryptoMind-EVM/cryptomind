"""轉換漏斗事件（PR-5，設計：docs/plans/2026-09-27-launch-readiness-design.md §4）。

訪客首次出現 → 問第 1／2 題 → 帶著訪客身分登入 → 新帳號 → 記第一筆帳 → 開早報 → 7 日回訪。
事件全部寫進既有 ``audit_logs``（不新增表），走 ``core.audit.audit_log`` 的 fire-and-forget：

- 只在 event loop 上記：audit_log 在 running loop 建 task；在 worker thread 裡呼叫會
  ``asyncio.run`` 開新 loop，觸發 ORM engine 換 loop 重建（core/orm/session.py），
  所以不在 loop 上時直接跳過（寧可少記一筆）
- 記錄失敗一律吞掉（保留 CancelledError）——統計絕不擋主流程
- 訪客事件不帶 IP／user agent／username，只存匿名 guest_id（uuid 本體，不含簽章）

統計查詢在本檔下半（``funnel_stats_sync``），端點是 ``/api/admin/stats/funnel``
（api/routers/admin/stats.py）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from api.utils import run_sync
from core.audit import audit_log

logger = logging.getLogger(__name__)

GUEST_FIRST_SEEN = "guest_first_seen"
GUEST_QUESTION = "guest_question"
GUEST_CONVERTED = "guest_converted"
JOURNAL_FIRST_ENTRY = "journal_first_entry"
BRIEF_ENABLED = "brief_enabled"


def _emit(
    action: str,
    request_data: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[funnel] %s 略過：不在 event loop 上", action)
        return
    try:
        audit_log(action, user_id=user_id, request_data=request_data)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 統計用，任何失敗都不擋請求
        logger.warning("[funnel] %s 記錄失敗: %s", action, exc)


def _guest_raw(guest_id: str) -> str:
    """cookie 值 ``<uuid>.<簽章>`` → 只留 uuid（簽章不進 DB）。"""
    return guest_id.rpartition(".")[0] or guest_id


def record_guest_first_seen(guest_id: str) -> None:
    """簽發新的 guest_id cookie 時。"""
    _emit(GUEST_FIRST_SEEN, {"guest_id": _guest_raw(guest_id)})


def record_guest_question(guest_id: str, n: int) -> None:
    """訪客成功拿到回答（n＝今天第幾題，扣額後的計數）。"""
    _emit(GUEST_QUESTION, {"guest_id": _guest_raw(guest_id), "n": int(n)})


def record_guest_converted(
    request: Any, user_id: str, method: str, is_new_user: bool
) -> None:
    """登入成功時帶著有效簽章的 guest_id cookie → 記下訪客與帳號的對應。

    簽章用 guest.py 同一個驗證；沒有 cookie 或被竄改就不記。
    """
    try:
        # 延遲 import：guest.py 也 import 本模組
        from api.routers.guest import GUEST_COOKIE, _valid_guest_id  # noqa: PLC0415

        value = request.cookies.get(GUEST_COOKIE, "")
        if not _valid_guest_id(value):
            return
        _emit(
            GUEST_CONVERTED,
            {
                "guest_id": _guest_raw(value),
                "method": method,
                "is_new_user": bool(is_new_user),
            },
            user_id=user_id,
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 統計用，絕不擋登入
        logger.warning("[funnel] guest_converted 記錄失敗: %s", exc)


def _journal_rows_upto(user_id: str, limit: int) -> int:
    """該使用者的帳本列數（含軟刪除），最多數到 limit——只需要判斷「是不是剛好這幾筆」。"""
    from core.database.base import DatabaseBase  # noqa: PLC0415

    row = DatabaseBase.query_one(
        "SELECT COUNT(*) AS n FROM "
        "(SELECT 1 FROM trade_journal WHERE user_id = %s LIMIT %s) t",
        (user_id, limit),
    )
    return int((row or {}).get("n") or 0)


async def record_journal_first_entry_if_first(
    user_id: str, source: str, added: int = 1
) -> None:
    """剛新增 ``added`` 筆後，帳本總列數剛好等於 added → 這是第一次記帳。

    多一個走索引的小查詢（LIMIT added+1）；查詢失敗只記 log。
    """
    if not user_id or added <= 0:
        return
    try:
        n = await run_sync(_journal_rows_upto, user_id, added + 1)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 統計用，絕不擋記帳
        logger.warning(
            "[funnel] journal_first_entry 查詢失敗 user=%s: %s", user_id, exc
        )
        return
    if n == added:
        _emit(JOURNAL_FIRST_ENTRY, {"source": source}, user_id=user_id)


def record_brief_enabled(user_id: str) -> None:
    """早報偏好從「關」切到「開」。"""
    _emit(BRIEF_ENABLED, user_id=user_id)


# ── Admin 統計（/api/admin/stats/funnel）────────────────────────────────────
# 訪客步驟以「窗口內首次出現的訪客」為基數、帳號步驟以「窗口內新帳號」為基數
# （users.created_at，錢包／Telegram／Google 都算），每一步都是同一群人的子集。

FUNNEL_WINDOWS = (7, 30)

# 參數：(days, days, days)
_FUNNEL_GUEST_SQL = """
    WITH cohort AS (
        SELECT DISTINCT request_data->>'guest_id' AS gid
        FROM audit_logs
        WHERE action = 'guest_first_seen'
          AND created_at >= NOW() - make_interval(days => %s)
          AND request_data->>'guest_id' IS NOT NULL
    ),
    asked AS (
        SELECT request_data->>'guest_id' AS gid, COUNT(*) AS n
        FROM audit_logs
        WHERE action = 'guest_question'
          AND created_at >= NOW() - make_interval(days => %s)
        GROUP BY 1
    ),
    conv AS (
        SELECT request_data->>'guest_id' AS gid,
               BOOL_OR((request_data->>'is_new_user')::boolean) AS is_new
        FROM audit_logs
        WHERE action = 'guest_converted'
          AND created_at >= NOW() - make_interval(days => %s)
        GROUP BY 1
    )
    SELECT
        COUNT(*) AS first_seen,
        COUNT(a.gid) AS asked_1,
        COUNT(a.gid) FILTER (WHERE a.n >= 2) AS asked_2,
        COUNT(v.gid) AS converted,
        COUNT(v.gid) FILTER (WHERE a.gid IS NOT NULL) AS converted_after_question,
        COUNT(v.gid) FILTER (WHERE v.is_new) AS converted_new
    FROM cohort c
    LEFT JOIN asked a ON a.gid = c.gid
    LEFT JOIN conv v ON v.gid = c.gid
"""

# 參數：(days,)
# 早報：切過開（brief_enabled 事件），或目前生效為開——有偏好列看 enabled，沒偏好列
# 照預設（綁 Telegram 的人開，同 core/daily_brief/schedule.effective_prefs），否則
# 綁 TG 預設開的人永遠不會有事件。
# 7 日回訪：註冊後 7 天內、且在註冊日之後的日曆日出現 chat_page_visited；
# 剛註冊的人還在觀察期，所以是下限（窗口越短越偏低）。
# 效能：cohort 只有窗口內的新帳號，每個 EXISTS 走 idx_audit_logs_user_id 查該使用者自己的
# 幾筆 audit 列；admin 端點另有 30/min 限流、在 run_sync 的 thread 跑，不佔 event loop。
_FUNNEL_ACCOUNT_SQL = """
    WITH cohort AS (
        SELECT u.user_id, u.created_at
        FROM users u
        WHERE u.created_at >= NOW() - make_interval(days => %s)
    )
    SELECT
        COUNT(*) AS new_accounts,
        COUNT(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM audit_logs al
            WHERE al.action = 'journal_first_entry' AND al.user_id = c.user_id
        )) AS journal_first_entry,
        COUNT(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM audit_logs al
            WHERE al.action = 'brief_enabled' AND al.user_id = c.user_id
        ) OR COALESCE(
            (SELECT p.enabled FROM user_brief_prefs p
             WHERE p.user_id = c.user_id AND p.user_set),
            EXISTS (SELECT 1 FROM telegram_bindings b WHERE b.user_id = c.user_id)
        )) AS brief_enabled,
        COUNT(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM audit_logs al
            WHERE al.action = 'chat_page_visited' AND al.user_id = c.user_id
              AND DATE(al.created_at) > DATE(c.created_at)
              AND al.created_at <= c.created_at + INTERVAL '7 days'
        )) AS returned_7d
    FROM cohort c
"""


def funnel_stats_sync() -> dict[str, Any]:
    """同步 psycopg 查詢（呼叫端走 run_sync）。每個窗口兩個查詢，全部參數化。"""
    from core.database.connection import get_connection  # noqa: PLC0415

    conn = get_connection()
    try:
        with conn.cursor() as c:
            windows = []
            for days in FUNNEL_WINDOWS:
                c.execute(_FUNNEL_GUEST_SQL, (days, days, days))
                g = c.fetchone()
                c.execute(_FUNNEL_ACCOUNT_SQL, (days,))
                a = c.fetchone()
                new_accounts = a[0]
                windows.append(
                    {
                        "days": days,
                        "guests_first_seen": g[0],
                        "guests_asked_1": g[1],
                        "guests_asked_2": g[2],
                        "guests_converted": g[3],
                        "guests_converted_after_question": g[4],
                        "guests_converted_new": g[5],
                        "new_accounts": new_accounts,
                        "journal_first_entry": a[1],
                        "brief_enabled": a[2],
                        "returned_7d": a[3],
                        "return_rate_7d": (
                            round(a[3] / new_accounts, 3) if new_accounts else 0.0
                        ),
                    }
                )
            return {"success": True, "windows": windows}
    finally:
        conn.close()
