"""Tool risk_level 資料層測試（Consent Gate 基礎）。

測：
- _TOOL_RISK_LEVELS 分級正確（high/medium/low）
- get_tool_risk_level 對未知 tool 回 low
- _TOOLS_SEED 的每個 tool 都能查到 risk_level（不漏）
- ToolMetadata 預設 risk_level='low'
- submit_kyc_application 是 high（普惠金融示範 tool）
- schema.py 的 risk_level 欄位定義存在（CREATE TABLE + reconcile）
"""

from __future__ import annotations

from unittest.mock import patch

from core.agents.tool_registry import ToolMetadata
from core.database.tools import (
    _TOOLS_SEED,
    get_tool_risk_level,
)


class TestGetToolRiskLevel:
    def test_high_risk_tools_classified(self):
        """接觸錢包資產 / 變更狀態的 tool 必須是 high。"""
        for tool_id in [
            "get_eth_balance",
            "get_erc20_token_balance",
            "get_address_transactions",
            "get_whale_alerts",
            "submit_kyc_application",
        ]:
            assert get_tool_risk_level(tool_id) == "high", f"{tool_id} 應為 high"

    def test_medium_risk_tools_classified(self):
        """讀外部不可信內容的 tool 必須是 medium。"""
        for tool_id in [
            "fetch_url",
            "web_search",
            "google_news",
            "aggregate_news",
            "check_token_security",
        ]:
            assert get_tool_risk_level(tool_id) == "medium", f"{tool_id} 應為 medium"

    def test_low_risk_query_tools(self):
        """純查詢 tool 必須是 low。"""
        for tool_id in [
            "get_crypto_price",
            "technical_analysis",
            "get_current_time_taipei",
            "get_fear_and_greed_index",
            "tw_stock_price",
            "us_stock_price",
        ]:
            assert get_tool_risk_level(tool_id) == "low", f"{tool_id} 應為 low"

    def test_unknown_tool_defaults_low(self):
        """未列出的 tool_id 一律 low（fail-safe，不誤擋）。"""
        assert get_tool_risk_level("totally_unknown_tool_xyz") == "low"

    def test_invalid_level_falls_back_to_low(self):
        """若 _TOOL_RISK_LEVELS 被注入非法值，get_tool_risk_level 应回 low。"""
        with patch.dict("core.database.tools._TOOL_RISK_LEVELS", {"bad_tool": "critical"}):
            assert get_tool_risk_level("bad_tool") == "low"


class TestToolsSeedCoverage:
    def test_every_seed_tool_has_resolvable_risk_level(self):
        """_TOOLS_SEED 每個 tool_id 都能查到 risk_level（不漏）。
        get_tool_risk_level 對未列出者回 low，所以這保證不會 KeyError。
        """
        for entry in _TOOLS_SEED:
            tool_id = entry["tool_id"]
            level = get_tool_risk_level(tool_id)
            assert level in {"low", "medium", "high"}, (
                f"{tool_id} 的 risk_level {level!r} 不合法"
            )

    def test_submit_kyc_application_in_seed(self):
        """普惠金融示範 tool 必須在 catalog。"""
        ids = [t["tool_id"] for t in _TOOLS_SEED]
        assert "submit_kyc_application" in ids

    def test_submit_kyc_application_seed_category(self):
        """submit_kyc_application 的 category 應為 finance。"""
        entry = next(
            t for t in _TOOLS_SEED if t["tool_id"] == "submit_kyc_application"
        )
        assert entry["category"] == "finance"


class TestToolMetadataDefault:
    def test_default_risk_level_is_low(self):
        """ToolMetadata 不指定 risk_level 時預設 low。"""
        m = ToolMetadata(
            name="x", description="d", input_schema={}, handler=None
        )
        assert m.risk_level == "low"

    def test_risk_level_can_be_set(self):
        m = ToolMetadata(
            name="x", description="d", input_schema={}, handler=None,
            risk_level="high",
        )
        assert m.risk_level == "high"


class TestSchemaRiskLevelColumn:
    """確認 schema.py 有把 risk_level 加進 CREATE TABLE 與 reconcile。"""

    def test_create_table_has_risk_level(self):
        # 讀 create_tool_tables 的 source，確認含 risk_level 欄位定義
        import inspect

        import core.database.schema as schema

        src = inspect.getsource(schema.create_tool_tables)
        assert "risk_level" in src
        assert "DEFAULT 'low'" in src

    def test_reconcile_has_risk_level(self):
        import inspect

        import core.database.schema as schema

        src = inspect.getsource(schema.reconcile_tool_tables)
        assert "risk_level" in src
        assert "ADD COLUMN IF NOT EXISTS risk_level" in src
