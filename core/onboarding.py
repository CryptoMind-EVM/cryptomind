"""新手三步的完成狀態（PR-7，docs/plans/2026-09-27-launch-readiness-design.md §5）。

全部從既有資料推，不存任何東西（使用者按「關閉」存在前端 localStorage）：
- holding：帳本有未刪除的紀錄——手記或鏈上同步寫進來的都算。
  單純「有綁錢包」不算：EVM 登入會自動寫 user_wallets，每個 EVM 使用者都會直接打勾，
  但他的早報仍然是空的。
- brief：生效的早報偏好開著、而且有送得到的管道（跟 cron 同一個判斷）。
- call：有沒取消的明確喊單（judgment_calls）。
"""

from __future__ import annotations

from typing import Dict

from core.database.base import DatabaseBase

_COUNTS_SQL = """
    SELECT
        EXISTS (
            SELECT 1 FROM trade_journal WHERE user_id = %s AND deleted_at IS NULL
        ) AS has_entry,
        EXISTS (
            SELECT 1 FROM judgment_calls WHERE user_id = %s AND status <> 'cancelled'
        ) AS has_call
"""


def onboarding_status(user_id: str) -> Dict[str, bool]:
    from core.daily_brief import store as brief_store
    from core.daily_brief.schedule import effective_prefs, is_deliverable

    counts = DatabaseBase.query_one(_COUNTS_SQL, (user_id, user_id)) or {}
    brief_on = False
    telegram_bound = False
    row = brief_store.get_prefs_row(user_id)
    if row:
        prefs = effective_prefs(row)
        brief_on = prefs.enabled and is_deliverable(prefs)
        telegram_bound = prefs.telegram_id is not None
    return {
        "holding": bool(counts.get("has_entry")),
        "brief": bool(brief_on),
        "call": bool(counts.get("has_call")),
        "telegram_bound": telegram_bound,
    }
