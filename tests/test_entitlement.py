"""Entitlement resolver 單元測試。

驗證 design 2026-08-13 §5.2 entitlement matrix 與 §15.2 fail-closed 不變量。
純邏輯測試（不打 DB），透過 resolve_entitlement(membership=...) 餵入會員 dict。
"""

from __future__ import annotations

import core.entitlement as entitlement_mod
from core.entitlement import resolve_entitlement, resolve_entitlement_for_user


# --------------------------------------------------------------------------- #
# Canonical 權益表（design §5.2）                                              #
# --------------------------------------------------------------------------- #
class TestEntitlementTable:
    def test_free_values_match_design(self):
        e = resolve_entitlement(membership={"tier": "free", "is_premium": False, "is_expired": False})
        assert e.tier == "free"
        assert e.is_premium is False
        assert e.wallet_snapshot_count == 1
        assert e.scheduled_wallets == 0
        assert e.telegram_alerts is False
        assert e.safety_history_days == 0
        assert e.weekly_summary is False

    def test_active_premium_values_match_design(self):
        e = resolve_entitlement(
            membership={"tier": "premium", "is_premium": True, "is_expired": False}
        )
        assert e.tier == "premium"
        assert e.is_premium is True
        assert e.is_expired is False
        assert e.wallet_snapshot_count == 5
        assert e.scheduled_wallets == 5
        assert e.telegram_alerts is True
        assert e.safety_history_days == 90
        assert e.weekly_summary is True


# --------------------------------------------------------------------------- #
# Fail-safe：過期 / 未知 / 空                                                  #
# --------------------------------------------------------------------------- #
class TestFailSafe:
    def test_expired_premium_degrades_to_free(self):
        """§15.2：過期 premium 不得保有付費能力。"""
        e = resolve_entitlement(
            membership={"tier": "premium", "is_premium": True, "is_expired": True}
        )
        assert e.tier == "free"  # 有效 tier 退化
        assert e.is_premium is False
        assert e.scheduled_wallets == 0
        assert e.telegram_alerts is False
        assert e.can_use_scheduled_monitoring is False

    def test_none_tier_defaults_to_free(self):
        e = resolve_entitlement(tier=None)
        assert e.tier == "free"
        assert e.is_premium is False

    def test_unknown_tier_defaults_to_free(self):
        e = resolve_entitlement(tier="does-not-exist")
        assert e.tier == "free"

    def test_is_expired_flag_preserved_when_free(self):
        """free tier 不會因 is_expired 被誤升級；is_expired 仍忠實回傳。"""
        e = resolve_entitlement(tier="free", is_expired=True)
        assert e.tier == "free"
        assert e.is_premium is False
        assert e.is_expired is True


# --------------------------------------------------------------------------- #
# can_use_scheduled_monitoring（cron gate 的核心判斷）                         #
# --------------------------------------------------------------------------- #
class TestScheduledMonitoringGate:
    def test_free_cannot_monitor(self):
        assert resolve_entitlement(tier="free").can_use_scheduled_monitoring is False

    def test_expired_premium_cannot_monitor(self):
        e = resolve_entitlement(tier="premium", is_expired=True)
        assert e.can_use_scheduled_monitoring is False

    def test_active_premium_can_monitor(self):
        e = resolve_entitlement(tier="premium", is_expired=False)
        assert e.can_use_scheduled_monitoring is True


# --------------------------------------------------------------------------- #
# resolve_entitlement_for_user（DB 載入器，monkeypatch 避免 DB）              #
# --------------------------------------------------------------------------- #
class TestForUserLoader:
    def test_empty_user_id_is_free(self, monkeypatch):
        # 不應觸及 DB。
        called = {"n": 0}

        def _boom(_uid):  # pragma: no cover - 不應被呼叫
            called["n"] += 1
            raise AssertionError("DB should not be hit for empty user_id")

        monkeypatch.setattr(entitlement_mod, "get_user_membership", _boom, raising=False)
        e = resolve_entitlement_for_user("")
        assert e.tier == "free"
        assert called["n"] == 0

    def test_active_premium_user(self, monkeypatch):
        monkeypatch.setattr(
            "core.database.user.get_user_membership",
            lambda uid: {"tier": "premium", "is_premium": True, "is_expired": False},
        )
        e = resolve_entitlement_for_user("user-1")
        assert e.tier == "premium"
        assert e.can_use_scheduled_monitoring is True

    def test_db_error_fails_closed_to_free(self, monkeypatch):
        """DB 不可用時 fail-safe：不得假設任何付費能力。"""

        def _raise(_uid):
            raise RuntimeError("DB down")

        monkeypatch.setattr("core.database.user.get_user_membership", _raise)
        e = resolve_entitlement_for_user("user-2")
        assert e.tier == "free"
        assert e.is_premium is False
        assert e.can_use_scheduled_monitoring is False


# --------------------------------------------------------------------------- #
# 不變性                                                                       #
# --------------------------------------------------------------------------- #
class TestImmutability:
    def test_entitlement_is_frozen(self):
        e = resolve_entitlement(tier="premium", is_expired=False)
        try:
            e.scheduled_wallets = 99  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("Entitlement should be frozen/immutable")
