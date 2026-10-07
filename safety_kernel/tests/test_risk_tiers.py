"""risk_tiers 單元測試：price impact 色階 + 安全訊號綜合分級。

涵蓋：綠/黃/橙/紅/黑燈邊界、block 優先、未驗證升級、低流動性訊號。
全離線——assess_risk 是純函式。
"""

from __future__ import annotations

import pytest

from safety_kernel.risk_tiers import (
    TIER_BLOCK_PCT,
    TIER_GREEN_PCT,
    TIER_RED_PCT,
    TIER_YELLOW_PCT,
    RiskAssessment,
    assess_risk,
)
from safety_kernel.safety_rules import TokenSafety


def _safety(**kw) -> TokenSafety:
    defaults = dict(
        address="EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
        verification="whitelist",
        symbol="USDt",
        name="USDt",
        holders_count=3_000_000,
        has_admin=False,
        exists=True,
    )
    defaults.update(kw)
    return TokenSafety(**defaults)


class TestPriceImpactTiers:
    @pytest.mark.parametrize(
        "pi,level,consent",
        [
            (0.0, "green", False),
            (0.5, "green", False),
            (TIER_GREEN_PCT, "yellow", False),  # 1% 邊界 → yellow
            (2.0, "yellow", False),
            (TIER_YELLOW_PCT, "orange", True),  # 3% 邊界 → orange（同意）
            (4.0, "orange", True),
            (TIER_RED_PCT, "red", True),  # 5% 邊界 → red（同意）
            (10.0, "red", True),
            (TIER_BLOCK_PCT, "block", False),  # 15% → 硬擋（無同意選項）
            (30.0, "block", False),
        ],
    )
    def test_tier_boundaries(self, pi, level, consent):
        risk = assess_risk(pi, _safety())
        assert risk.level == level
        assert risk.needs_consent is consent
        assert risk.blocked == (level == "block")

    def test_block_level_has_no_consent(self):
        # 硬擋 = 沒有同意選項，直接阻止。
        risk = assess_risk(50.0, _safety())
        assert risk.blocked and not risk.needs_consent
        assert any("嚴重虧損" in r for r in risk.reasons)

    def test_no_safety_none_allowed(self):
        # safety=None（例如原生 TON 對換不需要評估）→ 照常分級。
        risk = assess_risk(0.2, None)
        assert risk.level == "green"


class TestSafetySignals:
    def test_unverified_upgrades_green_to_yellow(self):
        risk = assess_risk(0.2, _safety(verification="none"))
        assert risk.level == "yellow"  # 未驗證 → 至少 yellow

    def test_token_does_not_exist_blocks(self):
        risk = assess_risk(0.0, _safety(exists=False, verification="none"))
        assert risk.blocked
        assert risk.level == "block"
        assert "不存在" in risk.reasons[0]

    def test_low_liquidity_signal(self):
        # 已驗證但持有者極少 → 等級不變（green），但 reasons 記錄低流動性。
        risk = assess_risk(0.2, _safety(holders_count=50))
        assert risk.level == "green"
        assert any("50" in r for r in risk.reasons)

    def test_low_liquidity_unverified_upgrades_to_yellow(self):
        # 未驗證 + 低流動性 → 至少 yellow。
        risk = assess_risk(0.2, _safety(verification="none", holders_count=50))
        assert risk.level == "yellow"

    def test_high_liquidity_verified_green(self):
        risk = assess_risk(0.2, _safety(verification="whitelist", holders_count=3_000_000))
        assert risk.level == "green"

    def test_has_admin_reason_recorded(self):
        risk = assess_risk(0.2, _safety(has_admin=True))
        assert any("admin" in r for r in risk.reasons)

    def test_unknown_safety_blocks(self):
        # TonAPI 不可用 → exists=False（fail-closed）→ block。
        risk = assess_risk(0.0, _safety(exists=False, verification="unknown"))
        assert risk.blocked

    def test_verified_strong_signals_do_not_downgrade(self):
        # 已驗證但高 price impact → 仍照色階（orange）。
        risk = assess_risk(4.0, _safety())
        assert risk.level == "orange"


class TestRiskAssessmentShape:
    def test_dataclass_defaults(self):
        r = RiskAssessment(level="green", needs_consent=False, blocked=False)
        assert r.reasons == []

    def test_reasons_order_block_priority(self):
        # blocked 優先於其他分級，reasons 保留。
        risk = assess_risk(50.0, _safety(exists=False, verification="none"))
        assert risk.level == "block"
        assert len(risk.reasons) >= 2  # 代幣不存在 + 價格影響過高
