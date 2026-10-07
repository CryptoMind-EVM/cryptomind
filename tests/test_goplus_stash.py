"""GoPlus raw info 側錄（_stash / pop_token_security_raw）測試。

驗證 HITL 場景 B 的 process-level stash 機制：側錄、pop 消費、多合約、清空。
"""

from __future__ import annotations

from core.tools.crypto_modules.goplus import (
    _last_token_security_raw,
    _stash_token_security_raw,
    pop_token_security_raw,
)


def _stash_sample(contract="0xabc", chain_id="1", **info_kwargs):
    info = {"token_name": "Test", "is_honeypot": "0", **info_kwargs}
    _stash_token_security_raw(contract, chain_id, info)


class TestTokenSecurityStash:
    def teardown_method(self):
        """每個測試後清空，避免跨測試污染。"""
        _last_token_security_raw.clear()

    def test_stash_then_pop_returns_data(self):
        """側錄後 pop 取回資料。"""
        _stash_sample(contract="0xabc", is_honeypot="1")
        stashed = pop_token_security_raw()

        assert len(stashed) == 1
        assert stashed[0]["contract_address"] == "0xabc"
        assert stashed[0]["chain_id"] == "1"
        assert stashed[0]["info"]["is_honeypot"] == "1"

    def test_pop_clears_stash(self):
        """pop 是消費式，取回後 stash 清空。"""
        _stash_sample()
        assert len(_last_token_security_raw) == 1

        pop_token_security_raw()
        assert _last_token_security_raw == []

    def test_multiple_stashes_kept_in_order(self):
        """一輪查多合約 → 全部保留，順序依 stash 順序。"""
        _stash_sample(contract="0xaaa")
        _stash_sample(contract="0xbbb")
        _stash_sample(contract="0xccc")

        stashed = pop_token_security_raw()
        assert [s["contract_address"] for s in stashed] == ["0xaaa", "0xbbb", "0xccc"]

    def test_pop_empty_returns_empty_list(self):
        """無側錄時 pop 回空 list（不報錯）。"""
        assert pop_token_security_raw() == []

    def test_pop_after_pop_returns_empty(self):
        """連續 pop：第二次空。"""
        _stash_sample()
        pop_token_security_raw()
        assert pop_token_security_raw() == []

    def test_raw_info_preserved_for_extract_risk_signals(self):
        """側錄的 info 可直接餵給 _extract_risk_signals（端到端可用性）。"""
        from core.tools.crypto_modules.goplus import _extract_risk_signals

        _stash_sample(contract="0xscam", is_honeypot="1", selfdestruct="1")
        stashed = pop_token_security_raw()
        signals = _extract_risk_signals(stashed[0]["info"])

        assert "is_honeypot" in signals["high_risk"]
        assert "selfdestruct" in signals["high_risk"]
        assert signals["verdict"] == "high_risk"
