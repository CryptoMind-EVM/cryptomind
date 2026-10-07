"""每日早報排程判定（純函式）：誰在這一小時到點。

design §7：綁 Telegram 的人預設開（DANNY 決策 #1）、本地 08:00、同一天不重送、
時區由語言推（zh → Asia/Taipei，其餘 UTC）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from core.daily_brief.schedule import (
    DEFAULT_SEND_HOUR,
    default_timezone_for_language,
    due_users,
    effective_prefs,
    is_due,
)

pytestmark = pytest.mark.unit


def _row(**kw):
    base = {
        "user_id": "u1",
        "language": "zh-TW",
        "display_name": "DANNY",
        "membership_tier": "premium",
        "telegram_id": 123,
        "prefs_user_id": None,
        # 測試裡給了 prefs_user_id 的列都當成使用者自己設的（c063）；自動建的列另外測
        "user_set": True,
        "enabled": None,
        "send_hour": None,
        "timezone": None,
        "channels": None,
        "include_spend": None,
        "last_sent_on": None,
    }
    base.update(kw)
    return base


class TestDefaults:
    def test_telegram_bound_without_row_is_enabled_by_default(self):
        p = effective_prefs(_row())
        assert p.enabled is True and p.send_hour == DEFAULT_SEND_HOUR
        assert p.timezone == "Asia/Taipei" and p.channels == ("telegram", "inapp", "baseapp")
        assert p.include_spend is True and p.prefs_user_set is False

    def test_unbound_without_row_is_disabled(self):
        """沒綁 Telegram 又沒設過偏好＝沒地方推，不預設開。"""
        p = effective_prefs(_row(telegram_id=None))
        assert p.enabled is False

    def test_language_drives_default_timezone(self):
        assert default_timezone_for_language("zh-TW") == "Asia/Taipei"
        assert default_timezone_for_language("zh-CN") == "Asia/Taipei"
        assert default_timezone_for_language("en") == "UTC"
        assert default_timezone_for_language(None) == "UTC"

    def test_row_values_win_and_bad_channels_are_dropped(self):
        p = effective_prefs(
            _row(
                prefs_user_id="u1",
                enabled=False,
                send_hour=7,
                timezone="Europe/Moscow",
                channels=["inapp", "bogus"],
                include_spend=False,
                last_sent_on=date(2026, 9, 12),
            )
        )
        assert p.enabled is False and p.send_hour == 7 and p.timezone == "Europe/Moscow"
        assert p.channels == ("inapp",) and p.include_spend is False
        assert p.last_sent_on == date(2026, 9, 12) and p.prefs_user_set is True

    def test_auto_created_row_only_keeps_last_sent_on(self):
        """c063：mark_sent／claim_day 替沒設過的人建的列只是記帳。以前「有列」就算自己設過：
        頻道變成 DB 預設（少了 baseapp）、解綁 Telegram 後仍是開的、空早報也補市場概況。"""
        auto = dict(
            prefs_user_id="u1",
            user_set=False,
            enabled=True,
            send_hour=8,
            timezone="Asia/Taipei",
            channels=["telegram", "inapp"],
            include_spend=True,
            last_sent_on=date(2026, 9, 27),
        )
        p = effective_prefs(_row(**auto))
        assert p.channels == ("telegram", "inapp", "baseapp")
        assert p.last_sent_on == date(2026, 9, 27), "冪等鍵照樣要讀到"
        assert p.enabled is True and p.prefs_user_set is False
        assert effective_prefs(_row(**auto, telegram_id=None)).enabled is False, (
            "解綁 Telegram 就跟沒設過一樣關掉"
        )
        assert effective_prefs(_row(**{**auto, "user_set": None})).prefs_user_set is False

    def test_send_hour_is_clamped(self):
        assert (
            effective_prefs(
                _row(prefs_user_id="u1", enabled=True, send_hour=99)
            ).send_hour
            == 23
        )


class TestIsDue:
    def test_due_at_local_hour_only(self):
        p = effective_prefs(_row())  # Asia/Taipei, 08:00
        assert (
            is_due(p, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)) is True
        )  # 08:05 台北
        assert (
            is_due(p, datetime(2026, 9, 13, 1, 5, tzinfo=timezone.utc)) is False
        )  # 09:05 台北
        assert (
            is_due(p, datetime(2026, 9, 12, 23, 59, tzinfo=timezone.utc)) is False
        )  # 07:59 台北

    def test_not_resent_same_local_day(self):
        p = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, last_sent_on=date(2026, 9, 13))
        )
        assert is_due(p, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)) is False
        # 隔天照送
        assert is_due(p, datetime(2026, 9, 14, 0, 5, tzinfo=timezone.utc)) is True

    def test_disabled_never_due(self):
        p = effective_prefs(_row(prefs_user_id="u1", enabled=False))
        assert is_due(p, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)) is False

    def test_telegram_only_channel_needs_binding(self):
        p = effective_prefs(
            _row(
                prefs_user_id="u1",
                enabled=True,
                channels=["telegram"],
                telegram_id=None,
            )
        )
        assert is_due(p, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)) is False
        # in-app 就不需要綁定
        p2 = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, channels=["inapp"], telegram_id=None)
        )
        assert is_due(p2, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)) is True

    def test_bad_timezone_falls_back_to_utc_not_crash(self):
        p = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, timezone="Mars/Olympus", send_hour=8)
        )
        assert is_due(p, datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)) is True

    def test_naive_now_is_treated_as_utc(self):
        p = effective_prefs(_row())
        assert is_due(p, datetime(2026, 9, 13, 0, 5)) is True


def test_due_users_skips_broken_rows():
    rows = [_row(), {"garbage": True}, _row(user_id="u2", language="en", telegram_id=9)]
    due = due_users(rows, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc))
    # u1 台北 08:05 到點；u2 是 UTC 00:05 未到點；壞列跳過
    assert [p.user_id for p in due] == ["u1"]
