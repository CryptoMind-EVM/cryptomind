"""誰該在這一小時收早報（純函式；cron 每小時跑一次）。

規則（design §7）：
- 沒有 prefs 列＝用預設：綁了 Telegram 的人**預設開**（DANNY 拍板決策 #1），
  沒綁的只有 in-app 且預設關（沒地方推）。
- 本地時＝``send_hour`` 且 ``last_sent_on`` ≠ 本地今天才算到點；同一小時 cron 若
  重跑也不會重送。
- 時區：``users`` 沒有 timezone 欄位，第一版用語言推（zh → Asia/Taipei，其餘 UTC），
  使用者可在 Settings 改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

DEFAULT_SEND_HOUR = 8
# baseapp＝Base App／Farcaster 宿主推播（2026-09-13）：有存到通知 token 的人才會真的送，
# 所以預設開不會多打擾任何人。
DEFAULT_CHANNELS = ("telegram", "inapp", "baseapp")
# email（PR-8）不在預設：確認信點了才會被加進 channels；只有訂閱有效（email_active）才算可送
VALID_CHANNELS = frozenset(DEFAULT_CHANNELS) | {"email"}


def default_timezone_for_language(language: Optional[str]) -> str:
    lang = (language or "").lower()
    if lang.startswith("zh"):
        return "Asia/Taipei"
    return "UTC"


@dataclass(frozen=True)
class BriefPrefs:
    user_id: str
    enabled: bool
    send_hour: int
    timezone: str
    channels: tuple = DEFAULT_CHANNELS
    include_spend: bool = True
    include_macro: bool = True
    last_sent_on: Optional[date] = None
    # 附帶的收件資訊（不是偏好，但 cron 逐人處理時一起帶）
    telegram_id: Optional[int] = None
    language: str = "zh-TW"
    display_name: Optional[str] = None
    membership_tier: str = "free"
    # Email 早報已確認且沒退訂（cron 在 EMAIL_BRIEF_ENABLED 開時才標，關的時候永遠 False）
    email_active: bool = False
    # 使用者自己設過偏好（設定頁／bot /brief／Email 訂閱）。系統記 last_sent_on 自動建的列不算（c063）
    prefs_user_set: bool = field(default=False, compare=False)
    account_created_at: Optional[datetime] = field(default=None, compare=False)

    def to_api(self) -> dict:
        return {
            "enabled": self.enabled,
            "send_hour": self.send_hour,
            "timezone": self.timezone,
            "channels": list(self.channels),
            "include_spend": self.include_spend,
            "include_macro": self.include_macro,
            "last_sent_on": self.last_sent_on.isoformat()
            if self.last_sent_on
            else None,
            "telegram_bound": self.telegram_id is not None,
        }


def _safe_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — 壞時區字串退回 UTC，不讓一個人卡住整批
        return ZoneInfo("UTC")


def effective_prefs(row: Mapping[str, Any]) -> BriefPrefs:
    """DB 的 LEFT JOIN 列（users × telegram_bindings × user_brief_prefs）→ 生效偏好。"""
    telegram_id = row.get("telegram_id")
    # 自動建的列（mark_sent／claim_day 記帳用）只取 last_sent_on，其他照「沒設過」的預設（c063）
    user_set = row.get("prefs_user_id") is not None and bool(row.get("user_set"))
    language = row.get("language") or "zh-TW"
    if user_set:
        enabled = bool(row.get("enabled"))
        send_hour = int(
            row.get("send_hour")
            if row.get("send_hour") is not None
            else DEFAULT_SEND_HOUR
        )
        tz = row.get("timezone") or default_timezone_for_language(language)
        raw_channels = row.get("channels") or list(DEFAULT_CHANNELS)
        channels = (
            tuple(c for c in raw_channels if c in VALID_CHANNELS) or DEFAULT_CHANNELS
        )
        include_spend = (
            bool(row.get("include_spend"))
            if row.get("include_spend") is not None
            else True
        )
        include_macro = (
            bool(row.get("include_macro"))
            if row.get("include_macro") is not None
            else True
        )
        last_sent_on = row.get("last_sent_on")
    else:
        enabled = telegram_id is not None
        send_hour = DEFAULT_SEND_HOUR
        tz = default_timezone_for_language(language)
        channels = DEFAULT_CHANNELS
        include_spend = True
        include_macro = True
        last_sent_on = row.get("last_sent_on")
    if isinstance(last_sent_on, datetime):
        last_sent_on = last_sent_on.date()
    return BriefPrefs(
        user_id=str(row["user_id"]),
        enabled=enabled,
        send_hour=max(0, min(23, send_hour)),
        timezone=tz,
        channels=channels,
        include_spend=include_spend,
        include_macro=include_macro,
        last_sent_on=last_sent_on,
        telegram_id=int(telegram_id) if telegram_id is not None else None,
        language=language,
        display_name=row.get("display_name"),
        membership_tier=str(row.get("membership_tier") or "free"),
        email_active=bool(row.get("email_active")),
        prefs_user_set=user_set,
        account_created_at=row.get("account_created_at"),
    )


def local_today(prefs: BriefPrefs, now_utc: datetime) -> date:
    return now_utc.astimezone(_safe_zone(prefs.timezone)).date()


def is_deliverable(prefs: BriefPrefs) -> bool:
    """有地方送：站內、Base App 宿主推播、綁了 Telegram 的 telegram 管道，或已確認的 email。

    baseapp 以前不算——只勾 Base App 的人永遠不會到點（PR-7 修）。推播只是敲門，
    內容寫在站內通知那份（cron_daily_brief.send_one）。
    """
    return (
        "inapp" in prefs.channels
        or "baseapp" in prefs.channels
        or ("telegram" in prefs.channels and prefs.telegram_id is not None)
        or ("email" in prefs.channels and prefs.email_active)
    )


def is_due(prefs: BriefPrefs, now_utc: datetime) -> bool:
    """到點＝啟用、有地方送、本地整點命中、今天還沒送過。"""
    if not prefs.enabled:
        return False
    if not is_deliverable(prefs):
        return False
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local = now_utc.astimezone(_safe_zone(prefs.timezone))
    if local.hour != prefs.send_hour:
        return False
    return prefs.last_sent_on != local.date()


def due_users(rows, now_utc: datetime) -> list[BriefPrefs]:
    out = []
    for row in rows:
        try:
            prefs = effective_prefs(row)
        except Exception:  # noqa: BLE001 — 單筆壞資料不中斷批次
            continue
        if is_due(prefs, now_utc):
            out.append(prefs)
    return out
