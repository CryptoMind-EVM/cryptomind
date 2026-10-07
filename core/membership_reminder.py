"""會員到期前站內提醒（2026-09-11 盤查）。

crypto 訂閱沒有自動續扣，到期前的提醒是留住續費率的唯一手段。以前完全沒有：
到期靠 NOW() 即時判定、前端只顯示到期日。

設計：不加排程器，掛在 ``GET /api/user/me``（每次 pageshow 都會打）——先用
純函式做免 DB 的預判（premium、未過期、剩 ≤ 7 天），命中才查最近通知做
3 天內去重，再寫一則 ``membership_expiring`` 站內通知。前端通知面板點了
跳 Settings 的會員卡。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.database.tools import normalize_membership_tier

REMINDER_WINDOW_DAYS = 7
REMINDER_COOLDOWN_DAYS = 3
NOTIFICATION_TYPE = "membership_expiring"


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif isinstance(value, datetime):
        dt = value
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def should_send_expiry_reminder(
    tier: Optional[str],
    expires_at: Any,
    now: datetime,
    last_sent_at: Any = None,
) -> bool:
    """premium、還沒過期、剩不到 REMINDER_WINDOW_DAYS，且 COOLDOWN 內沒提醒過。"""
    if normalize_membership_tier(tier) != "premium":
        return False
    exp = _as_utc(expires_at)
    if exp is None or exp <= now:
        return False  # 已過期：那是另一種訊息，不在這裡發
    if exp - now > timedelta(days=REMINDER_WINDOW_DAYS):
        return False
    last = _as_utc(last_sent_at)
    if last is not None and now - last < timedelta(days=REMINDER_COOLDOWN_DAYS):
        return False
    return True


async def maybe_notify_membership_expiring(
    user_id: str,
    tier: Optional[str],
    expires_at: Any,
    *,
    now: Optional[datetime] = None,
    repo: Any = None,
) -> bool:
    """命中條件就寫一則站內通知；回傳是否真的寫了。"""
    now = now or datetime.now(timezone.utc)
    if not user_id or not should_send_expiry_reminder(tier, expires_at, now):
        return False
    if repo is None:
        from core.orm.notifications_repo import notifications_repo as repo

    recent = await repo.get_notifications(user_id, limit=20)
    last_sent = None
    for n in recent:
        if n.get("type") != NOTIFICATION_TYPE:
            continue
        created = _as_utc(n.get("created_at"))
        if created and (last_sent is None or created > last_sent):
            last_sent = created
    if not should_send_expiry_reminder(tier, expires_at, now, last_sent):
        return False

    exp = _as_utc(expires_at)
    days_left = max(0, (exp - now).days)
    await repo.create_notification(
        user_id=user_id,
        notification_type=NOTIFICATION_TYPE,
        title="Premium 會員即將到期",
        body=f"你的 Premium 會員將於 {exp.strftime('%Y-%m-%d')} 到期（剩 {days_left} 天）。續費以免功能中斷。",
        data={"expires_at": exp.isoformat(), "days_left": days_left, "action": "renew"},
    )
    return True
