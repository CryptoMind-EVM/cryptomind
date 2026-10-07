"""Identity Trust Layer 單元測試（Trustworthy AI Hackathon — Principal 支柱）。

測 core/identity/trust.py 的身分信任評估：
- 錢包所有權（baseline）/ 鏈上年齡 / 活動度 / personhood stamps 的聚合
- 分數 0-100 與 tier 判定
- meets_risk_requirement 對 low/medium/high 的閘道
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.identity.trust import (
    MIN_TRUST_FOR_HIGH,
    MIN_TRUST_FOR_LOW,
    MIN_TRUST_FOR_MEDIUM,
    IdentityTrustAssessment,
    assess_identity_trust,
    meets_risk_requirement,
)


class TestAssessIdentityTrust:
    def test_unverified_wallet_is_anonymous(self):
        """未驗錢包 → 分數 0、tier anonymous、任何 medium/high 都不足。"""
        a = assess_identity_trust(wallet_verified=False)
        assert a.trust_score == 0
        assert a.tier == "anonymous"
        assert "low" in a.sufficient_for
        assert "medium" in a.insufficient_for
        assert "high" in a.insufficient_for

    def test_verified_wallet_only_is_known_wallet(self):
        """只過 ton_proof（無年齡/活動/stamp）→ baseline 30 分、known_wallet。"""
        a = assess_identity_trust(wallet_verified=True)
        assert a.trust_score == 30
        assert a.tier == "known_wallet"
        # 30 分足以做 low + medium（threshold 0/20），不足以做 high（50）
        assert "medium" in a.sufficient_for
        assert "high" in a.insufficient_for

    def test_wallet_age_increases_score(self):
        """錢包年齡滿 365 天 → 加 20 分（30 + 20 = 50，剛過 high threshold）。"""
        old = datetime.now(timezone.utc) - timedelta(days=400)
        a = assess_identity_trust(wallet_verified=True, wallet_first_active=old)
        assert a.trust_score >= 50
        assert "high" in a.sufficient_for

    def test_wallet_activity_increases_score(self):
        """活動度 50+ tx → 加 15 分。"""
        a = assess_identity_trust(wallet_verified=True, wallet_tx_count=60)
        assert a.trust_score == 45  # 30 baseline + 15 activity
        assert a.tier == "known_wallet"

    def test_soft_personhood_stamp_boosts(self):
        """Gitcoin Passport stamp（軟體式）→ 加 20 分。"""
        a = assess_identity_trust(
            wallet_verified=True,
            verified_stamps={"gitcoin_passport": True},
        )
        assert a.trust_score == 50  # 30 + 20
        assert "high" in a.sufficient_for  # 50 >= MIN_TRUST_FOR_HIGH

    def test_strong_personhood_stamp_boosts_more(self):
        """World ID Orb（強 personhood）→ 加 35 分，達 strong_verified tier。"""
        a = assess_identity_trust(
            wallet_verified=True,
            verified_stamps={"world_id": True},
        )
        assert a.trust_score == 65  # 30 + 35
        assert a.tier == "soft_verified"

    def test_multiple_signals_cap_at_100(self):
        """全部 signal 都滿 → 上限 100，tier strong_verified。"""
        old = datetime.now(timezone.utc) - timedelta(days=500)
        a = assess_identity_trust(
            wallet_verified=True,
            wallet_first_active=old,
            wallet_tx_count=100,
            verified_stamps={"world_id": True, "gitcoin_passport": True},
        )
        assert a.trust_score == 100
        assert a.tier == "strong_verified"

    def test_graceful_degradation_missing_signals(self):
        """缺失的 signal（None）不報錯，只是不計分。"""
        a = assess_identity_trust(
            wallet_verified=True,
            wallet_first_active=None,
            wallet_tx_count=None,
            verified_stamps=None,
        )
        assert a.trust_score == 30  # 只有 baseline

    def test_unknown_stamp_key_ignored(self):
        """未知的 stamp key（不在 soft/strong 清單）→ 忽略，不計分。"""
        a = assess_identity_trust(
            wallet_verified=True,
            verified_stamps={"unknown_random_stamp": True},
        )
        assert a.trust_score == 30  # 只有 baseline，未知 stamp 不加分

    def test_assessment_to_audit_metadata_structure(self):
        """to_audit_metadata 應產出可進 audit log JSONB 的結構。"""
        a = assess_identity_trust(wallet_verified=True)
        meta = a.to_audit_metadata()
        assert "trust_score" in meta
        assert "tier" in meta
        assert "signals" in meta
        assert isinstance(meta["signals"], list)
        assert "assessed_at" in meta


class TestMeetsRiskRequirement:
    def test_low_risk_always_meets(self):
        """low threshold = 0，任何分數都滿足。"""
        a = IdentityTrustAssessment(trust_score=0, tier="anonymous")
        assert meets_risk_requirement(a, "low") is True

    def test_medium_risk_needs_known_wallet(self):
        """medium threshold = 20。"""
        anon = IdentityTrustAssessment(trust_score=10, tier="anonymous")
        known = IdentityTrustAssessment(trust_score=30, tier="known_wallet")
        assert meets_risk_requirement(anon, "medium") is False
        assert meets_risk_requirement(known, "medium") is True

    def test_high_risk_needs_50(self):
        """high threshold = 50。"""
        low_score = IdentityTrustAssessment(trust_score=30, tier="known_wallet")
        high_score = IdentityTrustAssessment(trust_score=50, tier="known_wallet")
        assert meets_risk_requirement(low_score, "high") is False
        assert meets_risk_requirement(high_score, "high") is True

    def test_unknown_risk_level_defaults_to_pass(self):
        """未知 risk_level → threshold 0，放行（fail-open for unknown）。"""
        a = IdentityTrustAssessment(trust_score=0, tier="anonymous")
        assert meets_risk_requirement(a, "unknown_level") is True


class TestThresholdConstants:
    """確保閾值階層正確（high > medium > low）。"""

    def test_thresholds_are_monotonic(self):
        assert MIN_TRUST_FOR_LOW <= MIN_TRUST_FOR_MEDIUM <= MIN_TRUST_FOR_HIGH
        assert MIN_TRUST_FOR_LOW == 0
        assert MIN_TRUST_FOR_MEDIUM == 20
        assert MIN_TRUST_FOR_HIGH == 50
