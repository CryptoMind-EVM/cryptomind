"""會員到期前提醒（core/membership_reminder.py，2026-09-11 盤查 🟡 項）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from core.membership_reminder import (
    NOTIFICATION_TYPE,
    maybe_notify_membership_expiring,
    should_send_expiry_reminder,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)


class TestDecision:
    def test_premium_expiring_within_window_sends(self):
        assert should_send_expiry_reminder("premium", NOW + timedelta(days=3), NOW)

    def test_iso_string_expiry_is_accepted(self):
        assert should_send_expiry_reminder(
            "premium", (NOW + timedelta(days=1)).isoformat(), NOW
        )

    def test_free_tier_never_sends(self):
        assert not should_send_expiry_reminder("free", NOW + timedelta(days=1), NOW)

    def test_far_from_expiry_does_not_send(self):
        assert not should_send_expiry_reminder("premium", NOW + timedelta(days=30), NOW)

    def test_already_expired_does_not_send(self):
        assert not should_send_expiry_reminder("premium", NOW - timedelta(hours=1), NOW)

    def test_cooldown_blocks_repeat(self):
        exp = NOW + timedelta(days=2)
        assert not should_send_expiry_reminder(
            "premium", exp, NOW, NOW - timedelta(days=1)
        )
        assert should_send_expiry_reminder("premium", exp, NOW, NOW - timedelta(days=4))

    def test_garbage_expiry_does_not_send(self):
        assert not should_send_expiry_reminder("premium", "not-a-date", NOW)
        assert not should_send_expiry_reminder("premium", None, NOW)


class TestNotify:
    async def test_creates_notification_when_due(self):
        repo = AsyncMock()
        repo.get_notifications.return_value = []
        sent = await maybe_notify_membership_expiring(
            "u1", "premium", NOW + timedelta(days=2), now=NOW, repo=repo
        )
        assert sent is True
        kwargs = repo.create_notification.call_args.kwargs
        assert kwargs["notification_type"] == NOTIFICATION_TYPE
        assert kwargs["data"]["days_left"] == 2
        assert "2026-09-13" in kwargs["body"]

    async def test_dedupes_against_recent_reminder(self):
        repo = AsyncMock()
        repo.get_notifications.return_value = [
            {
                "type": NOTIFICATION_TYPE,
                "created_at": (NOW - timedelta(days=1)).isoformat(),
            },
            {"type": "friend_request", "created_at": NOW.isoformat()},
        ]
        sent = await maybe_notify_membership_expiring(
            "u1", "premium", NOW + timedelta(days=2), now=NOW, repo=repo
        )
        assert sent is False
        repo.create_notification.assert_not_called()

    async def test_no_db_hit_when_not_due(self):
        repo = AsyncMock()
        sent = await maybe_notify_membership_expiring(
            "u1", "premium", NOW + timedelta(days=60), now=NOW, repo=repo
        )
        assert sent is False
        repo.get_notifications.assert_not_called()
