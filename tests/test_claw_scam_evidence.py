"""claw_loop _build_scam_evidence 攔截邏輯測試。

驗證 Phase D：偵測 used_tools 含 GoPlus 工具 → 從 stash 取 raw → 組證據鏈。
只測 _build_scam_evidence 純函式（不跑完整 ReAct）。
"""

from __future__ import annotations

from core.agents.manager.claw_loop import _build_scam_evidence
from core.tools.crypto_modules.goplus import (
    _last_token_security_raw,
    _stash_token_security_raw,
)


class TestBuildScamEvidence:
    def teardown_method(self):
        _last_token_security_raw.clear()

    def test_no_security_tools_returns_none(self):
        """used_tools 不含 GoPlus 工具 → None（不顯示卡片）。"""
        assert _build_scam_evidence(["get_crypto_price", "web_search"]) is None

    def test_empty_used_tools_returns_none(self):
        assert _build_scam_evidence([]) is None
        assert _build_scam_evidence(None) is None

    def test_no_stashed_raw_returns_none(self):
        """用了 GoPlus 但 stash 空（異常）→ None。"""
        result = _build_scam_evidence(["check_token_security"])
        assert result is None

    def test_high_risk_verdict_builds_evidence(self):
        """stash 有高風險 raw → 組完整證據鏈。"""
        _stash_token_security_raw(
            "0xscam", "1", {"is_honeypot": "1", "is_open_source": "0"}
        )
        result = _build_scam_evidence(["check_token_security"])

        assert result is not None
        assert result["verdict"] == "high_risk"
        assert result["confidence"] >= 65  # 高風險下限
        assert result["requires_ack"] is True
        assert result["target"] == "0xscam"
        assert "breakdown" in result
        assert "signals" in result
        # breakdown 含 honeypot
        signals_in_breakdown = {b["signal"] for b in result["breakdown"]}
        assert "is_honeypot" in signals_in_breakdown

    def test_normal_verdict_returns_none(self):
        """verdict=normal（乾淨代幣）→ 不顯示卡片。"""
        _stash_token_security_raw(
            "0xclean",
            "1",
            {"is_open_source": "1", "is_in_dex": "1", "trust_list": "1"},
        )
        result = _build_scam_evidence(["check_token_security"])
        assert result is None  # normal 不在 _SCAM_EVIDENCE_VERDICTS

    def test_warning_verdict_builds_evidence(self):
        """verdict=warning → 顯示卡片。"""
        _stash_token_security_raw(
            "0xwarn", "1", {"is_open_source": "0"}  # 觸發 not_open_source 警告
        )
        result = _build_scam_evidence(["check_token_security"])
        assert result is not None
        assert result["verdict"] == "warning"

    def test_pop_clears_stash_after_build(self):
        """_build_sam_evidence 消費 stash（pop），第二次呼叫為 None。"""
        _stash_token_security_raw("0xscam", "1", {"is_honeypot": "1"})
        first = _build_scam_evidence(["check_token_security"])
        assert first is not None

        # 第二次：stash 已被 pop 清空
        second = _build_scam_evidence(["check_token_security"])
        assert second is None

    def test_address_safety_tool_also_detected(self):
        """check_address_safety 也算 GoPlus 詐騙判定工具。"""
        _stash_token_security_raw("0xaddr", "1", {"is_honeypot": "1"})
        result = _build_scam_evidence(["check_address_safety"])
        # 注意：address safety 走不同工具但共用 stash；只要有 high_risk 就組
        assert result is not None
