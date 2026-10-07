"""safety_rules 單元測試：TokenSafety 資料結構 + build_token_safety 訊號規則。

全離線——build_token_safety 是純函式（網路層在主平台）。
"""

from __future__ import annotations

from safety_kernel.safety_rules import (
    LOW_HOLDER_THRESHOLD,
    TokenSafety,
    build_token_safety,
    safety_to_dict,
)


class TestTokenSafetyDerivedSignals:
    def test_whitelist_is_verified(self):
        s = TokenSafety(
            address="EQ1",
            verification="whitelist",
            symbol="USDt",
            name="USDt",
            holders_count=3_000_000,
            has_admin=False,
            exists=True,
        )
        assert s.is_verified
        assert not s.is_low_liquidity

    def test_non_whitelist_not_verified(self):
        for v in ("none", "blacklist", "unknown"):
            s = TokenSafety(
                address="EQ1", verification=v, symbol="", name="", holders_count=1, has_admin=False, exists=True
            )
            assert not s.is_verified

    def test_low_liquidity_threshold(self):
        low = TokenSafety(
            address="EQ1", verification="whitelist", symbol="X", name="X", holders_count=LOW_HOLDER_THRESHOLD - 1, has_admin=False, exists=True
        )
        ok = TokenSafety(
            address="EQ1", verification="whitelist", symbol="X", name="X", holders_count=LOW_HOLDER_THRESHOLD, has_admin=False, exists=True
        )
        assert low.is_low_liquidity
        assert not ok.is_low_liquidity

    def test_nonexistent_token_is_not_low_liquidity(self):
        # 不存在的代幣：exists=False → 不算低流動性（它是「不存在」訊號）。
        s = TokenSafety(address="EQ1", verification="none", symbol="", name="", holders_count=0, has_admin=False, exists=False)
        assert not s.is_low_liquidity


class TestBuildTokenSafety:
    def test_standard_signals_accumulated(self):
        s = build_token_safety(
            address="EQ1",
            verification="none",
            symbol="SCAM",
            name="Scam Token",
            holders_count=42,
            has_admin=True,
            exists=True,
        )
        assert not s.is_verified
        assert s.is_low_liquidity
        texts = " | ".join(s.signals)
        assert "not officially verified" in texts
        assert "42 holders" in texts
        assert "admin" in texts

    def test_verified_clean_no_signals(self):
        s = build_token_safety(
            address="EQ1",
            verification="whitelist",
            symbol="USDt",
            name="USDt",
            holders_count=3_000_000,
            has_admin=False,
            exists=True,
        )
        assert s.signals == []

    def test_extra_signals_appended(self):
        s = build_token_safety(
            address="EQ1",
            verification="unknown",
            symbol="",
            name="",
            holders_count=0,
            has_admin=False,
            exists=False,
            extra_signals=["Unable to complete safety assessment: TonAPI unavailable"],
        )
        assert any("TonAPI unavailable" in sig for sig in s.signals)

    def test_int_coercion(self):
        s = build_token_safety(
            address="EQ1",
            verification="whitelist",
            symbol="X",
            name="X",
            holders_count="12345",
            has_admin=1,
            exists="yes",
        )
        assert s.holders_count == 12345
        assert s.has_admin is True
        assert s.exists is True


class TestSafetyToDict:
    def test_serializes_all_fields(self):
        s = build_token_safety(
            address="EQ1",
            verification="whitelist",
            symbol="USDt",
            name="USDt",
            holders_count=100,
            has_admin=False,
            exists=True,
        )
        d = safety_to_dict(s)
        assert d["address"] == "EQ1"
        assert d["is_verified"] is True
        assert d["is_low_liquidity"] is True
        assert d["exists"] is True
        assert d["source"] == "tonapi"
        assert isinstance(d["signals"], list)
