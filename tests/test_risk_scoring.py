"""risk_scoring 信心分計算測試。

驗證加權表 + 各信號情境 + 高風險下限 + 可解釋性（breakdown 帶 reason）。
對齊 _extract_risk_signals 的 dict 結構（不測 goplus raw，只測 scoring 純函式）。
"""

from __future__ import annotations

from core.agents.risk_scoring import score_scam_confidence


def _signals(**kwargs) -> dict:
    """建一個 _extract_risk_signals 風格的 dict。"""
    base = {
        "high_risk": [],
        "warnings": [],
        "safeties": [],
        "metrics": {},
        "is_trusted": False,
        "verdict": "insufficient",
    }
    base.update(kwargs)
    return base


class TestScoreScamConfidence:
    def test_clean_token_low_confidence(self):
        """無任何風險信號 → confidence 接近 base（10），verdict=insufficient/normal。"""
        result = score_scam_confidence(_signals())
        assert result["confidence"] == 10  # base，無加減
        assert result["base"] == 10
        assert result["breakdown"] == []

    def test_honeypot_hits_high_risk_floor(self):
        """is_honeypot +35，但觸發高風險下限 65。"""
        result = score_scam_confidence(
            _signals(high_risk=["is_honeypot"], verdict="high_risk")
        )
        # base 10 + 35 = 45，但 high_risk 下限 65 → 65
        assert result["confidence"] == 65
        assert result["verdict"] == "high_risk"
        # breakdown 含 honeypot 加權
        weights = {b["signal"]: b["weight"] for b in result["breakdown"]}
        assert weights["is_honeypot"] == 35

    def test_multiple_high_risk_signals_capped_at_100(self):
        """多個高風險信號 → confidence 上限 100。"""
        result = score_scam_confidence(
            _signals(
                high_risk=["is_honeypot", "selfdestruct", "cannot_buy"],
                verdict="high_risk",
            )
        )
        # 10 + 35*3 = 115 → clamp 100
        assert result["confidence"] == 100

    def test_warning_only_no_floor(self):
        """純警告（無高風險）→ 不套下限，分數較低。"""
        result = score_scam_confidence(
            _signals(warnings=["hidden_owner", "not_open_source"], verdict="warning")
        )
        # 10 + 12 + 10 = 32
        assert result["confidence"] == 32

    def test_safety_signal_reduces_confidence(self):
        """安全信號（trust_list -30）抵扣。"""
        result = score_scam_confidence(
            _signals(
                warnings=["hidden_owner"], safeties=["trust_list"], verdict="warning"
            )
        )
        # 10 + 12 - 30 = -8 → clamp 0
        assert result["confidence"] == 0

    def test_safety_cannot_override_high_risk_floor(self):
        """高風險信號存在時，安全信號抵扣後仍套下限 65。"""
        result = score_scam_confidence(
            _signals(
                high_risk=["is_honeypot"],
                safeties=["trust_list", "is_in_cex"],
                verdict="high_risk",
            )
        )
        # 10 + 35 - 30 - 25 = -10 → clamp 0，但 high_risk 下限 65 → 65
        assert result["confidence"] == 65

    def test_breakdown_contains_reason_for_explainability(self):
        """breakdown 每項帶中文 reason（評審可解釋性）。"""
        result = score_scam_confidence(
            _signals(high_risk=["is_honeypot"], warnings=["hidden_owner"])
        )
        for item in result["breakdown"]:
            assert "signal" in item
            assert "weight" in item
            assert "reason" in item
            assert isinstance(item["reason"], str) and item["reason"]
        # 確認 reason 是中文（honeypot）
        honeypot_item = next(b for b in result["breakdown"] if b["signal"] == "is_honeypot")
        assert "蜜罐" in honeypot_item["reason"]

    def test_verdict_passed_through(self):
        """verdict 直接帶 signals 的 verdict。"""
        for v in ["high_risk", "warning", "trusted_with_permissions", "normal", "insufficient"]:
            result = score_scam_confidence(_signals(verdict=v))
            assert result["verdict"] == v

    def test_unknown_signal_ignored(self):
        """未知信號 key（不在加權表）→ 不計分、不進 breakdown。"""
        result = score_scam_confidence(
            _signals(warnings=["some_unknown_signal"], verdict="warning")
        )
        assert result["confidence"] == 10  # 只有 base
        assert all(b["signal"] != "some_unknown_signal" for b in result["breakdown"])
