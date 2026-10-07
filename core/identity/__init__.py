"""Identity Trust Layer — Principal 支柱。

可插拔的信任信號聚合，把錢包所有權 + 鏈上信號 + 外部 personhood stamps
聚合成 0-100 的 trust_score，供 Consent Gate 做風險分級決策。
"""

from core.identity.trust import (
    MIN_TRUST_FOR_HIGH,
    MIN_TRUST_FOR_LOW,
    MIN_TRUST_FOR_MEDIUM,
    IdentityTrustAssessment,
    TrustSignal,
    assess_identity_trust,
    meets_risk_requirement,
)

__all__ = [
    "IdentityTrustAssessment",
    "TrustSignal",
    "assess_identity_trust",
    "meets_risk_requirement",
    "MIN_TRUST_FOR_LOW",
    "MIN_TRUST_FOR_MEDIUM",
    "MIN_TRUST_FOR_HIGH",
]
