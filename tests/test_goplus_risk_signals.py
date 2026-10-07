"""GoPlus _extract_risk_signals 結構化風險信號萃取測試。

驗證從 GoPlus raw API 結果萃取出的結構化 dict 與既有 _format_token_security
的 markdown 報告口徑一致（同一套欄位判斷），供後續 HITL 場景（證據鏈展示 /
信心分計算）使用。

不測 markdown 輸出（那由既有 check_token_security 既有路徑負責），只測結構化萃取。
"""

from __future__ import annotations

from core.tools.crypto_modules.goplus import _extract_risk_signals


def _base_info(**overrides) -> dict:
    """建一個 GoPlus raw info 樣本，預設全部安全欄位為 '0'（無風險）。"""
    base = {
        "token_name": "TestToken",
        "token_symbol": "TST",
        "is_honeypot": "0",
        "cannot_buy": "0",
        "is_mintable": "0",
        "owner_change_balance": "0",
        "selfdestruct": "0",
        "honeypot_with_same_creator": "0",
        "is_open_source": "1",
        "hidden_owner": "0",
        "slippage_modifiable": "0",
        "is_proxy": "0",
        "transfer_pausable": "0",
        "external_call": "0",
        "trust_list": "0",
        "is_in_dex": "0",
        "buy_tax": "0",
        "sell_tax": "0",
        "owner_percent": "5",
        "creator_percent": "3",
        "holder_count": "1000",
        "total_supply": "1000000",
        "holders": [],
    }
    base.update(overrides)
    return base


class TestExtractRiskSignals:
    def test_clean_token_returns_normal_verdict(self):
        """乾淨代幣（開源、無風險、在 DEX）→ verdict=normal，無 high_risk/warnings。"""
        info = _base_info(is_in_dex="1")
        result = _extract_risk_signals(info)

        assert result["high_risk"] == []
        assert result["warnings"] == []
        assert "is_in_dex" in result["safeties"]
        assert result["verdict"] == "normal"
        assert result["is_trusted"] is True

    def test_honeypot_detected_as_high_risk(self):
        """is_honeypot=1 → high_risk 含 is_honeypot，verdict=high_risk。"""
        info = _base_info(is_honeypot="1")
        result = _extract_risk_signals(info)

        assert "is_honeypot" in result["high_risk"]
        assert result["verdict"] == "high_risk"
        assert result["is_trusted"] is False

    def test_selfdestruct_detected_as_high_risk(self):
        """selfdestruct=1 → high_risk。"""
        info = _base_info(selfdestruct="1")
        result = _extract_risk_signals(info)

        assert "selfdestruct" in result["high_risk"]
        assert result["verdict"] == "high_risk"

    def test_mintable_with_owner_change_balance_is_high_risk(self):
        """is_mintable=1 + owner_change_balance=1 → mintable_and_owner_change_balance（高風險）。"""
        info = _base_info(is_mintable="1", owner_change_balance="1")
        result = _extract_risk_signals(info)

        assert "mintable_and_owner_change_balance" in result["high_risk"]
        assert result["verdict"] == "high_risk"

    def test_mintable_without_owner_change_is_warning(self):
        """is_mintable=1 但 owner_change_balance=0 → 只進 warnings（mintable）。"""
        info = _base_info(is_mintable="1", owner_change_balance="0")
        result = _extract_risk_signals(info)

        assert "mintable" in result["warnings"]
        assert "mintable_and_owner_change_balance" not in result["high_risk"]
        assert result["verdict"] == "warning"

    def test_not_open_source_is_warning(self):
        """is_open_source=0 → warnings 含 not_open_source。"""
        info = _base_info(is_open_source="0")
        result = _extract_risk_signals(info)

        assert "not_open_source" in result["warnings"]
        assert result["verdict"] == "warning"

    def test_trusted_token_with_risks_downgrades_verdict(self):
        """trust_list=1 + is_honeypot=1 → verdict=trusted_with_permissions（不報 high_risk）。

        對齊 _format_token_security:258-262：合規代幣（如 USDT）即使有 mintable
        等權限也降級，因為是正常營運需要。
        """
        info = _base_info(trust_list="1", is_honeypot="1")
        result = _extract_risk_signals(info)

        assert "is_honeypot" in result["high_risk"]  # 信號仍記錄
        assert result["is_trusted"] is True
        assert result["verdict"] == "trusted_with_permissions"

    def test_cex_listed_counts_as_safety(self):
        """is_in_cex.listed=1 → safeties 含 is_in_cex。"""
        info = _base_info(is_in_cex={"listed": "1", "cex_list": ["Binance"]})
        result = _extract_risk_signals(info)

        assert "is_in_cex" in result["safeties"]
        assert result["is_trusted"] is True

    def test_insufficient_data_verdict(self):
        """無任何風險/警告/安全信號 → verdict=insufficient。"""
        info = _base_info()  # 全 0、is_open_source=1 但無任何 safety
        result = _extract_risk_signals(info)

        # is_open_source=1 不算 safety（它只是「沒有 not_open_source 警告」）
        assert result["high_risk"] == []
        assert result["warnings"] == []
        assert result["safeties"] == []
        assert result["verdict"] == "insufficient"

    def test_metrics_extraction(self):
        """數值指標正確萃取。"""
        info = _base_info(
            buy_tax="5",
            sell_tax="10",
            owner_percent="20",
            holder_count="500",
            total_supply="999",
            holders=[
                {"percent": 0.3},
                {"percent": 0.2},
                {"percent": 0.1},
            ],
        )
        result = _extract_risk_signals(info)

        assert result["metrics"]["buy_tax"] == "5"
        assert result["metrics"]["sell_tax"] == "10"
        assert result["metrics"]["owner_percent"] == "20"
        assert result["metrics"]["holder_count"] == "500"
        assert result["metrics"]["total_supply"] == "999"
        # top3 = (0.3 + 0.2 + 0.1) * 100 = 60.0
        assert result["metrics"]["top3_holder_percent"] == 60.0

    def test_top3_holder_percent_none_when_insufficient_holders(self):
        """holders < 3 → top3_holder_percent=None。"""
        info = _base_info(holders=[{"percent": 0.5}])
        result = _extract_risk_signals(info)

        assert result["metrics"]["top3_holder_percent"] is None

    def test_consistency_with_format_token_security_verdict(self):
        """與 _format_token_security 的 verdict 口徑一致性抽樣驗證。

        同一份 info 餵進兩個函式，verdict 字串應對應：
        - extract 的 "high_risk" ↔ format 的「高風險」
        - extract 的 "warning" ↔ format 的「有風險訊號」
        確保未來 HITL 用 extract 的結構化資料時，口徑與 LLM 看到的 markdown 一致。
        """
        from core.tools.crypto_modules.goplus import _format_token_security

        # honeypot 案例
        info = _base_info(is_honeypot="1")
        extract_v = _extract_risk_signals(info)["verdict"]
        format_md = _format_token_security(info, "1")
        assert extract_v == "high_risk"
        assert "High Risk" in format_md

        # warning 案例
        info = _base_info(is_open_source="0")
        extract_v = _extract_risk_signals(info)["verdict"]
        format_md = _format_token_security(info, "1")
        assert extract_v == "warning"
        assert "Risk signals" in format_md
