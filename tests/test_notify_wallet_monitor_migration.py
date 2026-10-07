"""notify_wallet_monitor_migration 決策邏輯測試（design §9.7 policy B）。

只測純函式 _should_notify；不打 DB、不發通知。
"""

from __future__ import annotations

from core.entitlement import resolve_entitlement
from scripts.notify_wallet_monitor_migration import _should_notify

PREMIUM = resolve_entitlement(tier="premium", is_expired=False)
FREE = resolve_entitlement(tier="free")
EXPIRED_PREMIUM = resolve_entitlement(tier="premium", is_expired=True)


class TestShouldNotify:
    def test_free_unnotified_is_eligible(self):
        ok, reason = _should_notify({"monitored_wallets": ["EQx"]}, FREE)
        assert (ok, reason) == (True, "eligible")

    def test_active_premium_skipped(self):
        """active premium 不受 gate 影響，不通知。"""
        ok, reason = _should_notify({"monitored_wallets": ["EQx"]}, PREMIUM)
        assert (ok, reason) == (False, "premium_unaffected")

    def test_expired_premium_is_eligible(self):
        """過期 premium 會被 gate 停掉監測 → 應通知（退化成 free 權益）。"""
        ok, reason = _should_notify({"monitored_wallets": ["EQx"]}, EXPIRED_PREMIUM)
        assert ok is True

    def test_already_notified_skipped(self):
        """冪等：已標記 monitor_migration_notified → 跳過。"""
        settings = {"monitored_wallets": ["EQx"], "monitor_migration_notified": True}
        ok, reason = _should_notify(settings, FREE)
        assert (ok, reason) == (False, "already_notified")

    def test_notified_flag_takes_priority_for_free(self):
        """Free 且已通知 → already_notified（不重複）。"""
        settings = {"monitor_migration_notified": True}
        ok, reason = _should_notify(settings, FREE)
        assert ok is False

    def test_premium_takes_priority_over_notified_flag(self):
        """即便誤標 notified，active premium 仍歸 premium_unaffected。"""
        settings = {"monitor_migration_notified": True}
        ok, reason = _should_notify(settings, PREMIUM)
        assert (ok, reason) == (False, "premium_unaffected")
