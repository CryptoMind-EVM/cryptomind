"""MCP loader 治理映射層測試 — core/tools/mcp_loader.py。

PoC 重點不是「真的連 MCP server」（那需要 npx 子程序，CI 環境不適合），
而是驗證治理映射層：MCP tool 載入後的 risk_level 決策正確，不會繞過 Consent Gate。

測試用 mock langchain_mcp_adapters，聚焦於：
- MCP_ENABLED 預設關閉（不載入、不強迫依賴）
- 預設 risk_level=low
- MCP_TOOL_RISK_OVERRIDES 可覆寫，但只接受合法值
- register_mcp_risk_overrides 不覆蓋既有 tool 的風險等級
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.database.tools import (
    _TOOL_RISK_LEVELS,
    get_tool_risk_level,
    register_mcp_risk_overrides,
)
from core.tools.mcp_loader import (
    _build_registry_entries,
    _is_enabled,
    _resolve_risk_overrides,
    _resolve_servers,
    load_mcp_tools_sync,
)

# ──────────────────────────────────────────────────────────────────────────────
# Enable / config 開關
# ──────────────────────────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    assert _is_enabled() is False


def test_enabled_when_set(monkeypatch):
    for val in ("1", "true", "yes", "TRUE"):
        monkeypatch.setenv("MCP_ENABLED", val)
        assert _is_enabled() is True


def test_load_returns_empty_when_disabled(monkeypatch):
    """MCP_ENABLED 未設時，load_mcp_tools_sync 應直接回空 list，不嘗試連線。"""
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    with patch("core.tools.mcp_loader._fetch_mcp_tools") as mock_fetch:
        assert load_mcp_tools_sync() == []
        mock_fetch.assert_not_called()


def test_load_disabled_logs_env_value(monkeypatch, caplog):
    """disabled 時應記錄 env 值，讓「env 沒注入」能被 log 看見。"""
    import logging

    monkeypatch.delenv("MCP_ENABLED", raising=False)
    with caplog.at_level(logging.INFO, logger="API"):
        load_mcp_tools_sync()
    # 應有 [MCP] disabled 訊息，且含 env 值（None）
    assert any("[MCP] disabled" in r.message for r in caplog.records)


def test_default_servers(monkeypatch):
    """預設 server 清單含 crypto-trader（Python/stdio）。"""
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    monkeypatch.delenv("MCP_EXTRA_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    servers = _resolve_servers()
    assert "crypto-trader" in servers
    assert servers["crypto-trader"]["transport"] == "stdio"
    assert servers["crypto-trader"]["command"] == "python"


def test_manifund_server_gated_by_flag(monkeypatch):
    """manifund 預設 server 需 MANIFUND_MCP_ENABLED=1 才出現。"""
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    monkeypatch.delenv("MCP_EXTRA_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    assert "manifund" not in _resolve_servers()
    monkeypatch.setenv("MANIFUND_MCP_ENABLED", "1")
    servers = _resolve_servers()
    assert servers["manifund"]["transport"] == "streamable_http"
    assert servers["manifund"]["url"] == "https://manifund.org/api/mcp"


def test_custom_servers_merge_not_replace(monkeypatch):
    """MCP_SERVERS 為 merge 語意：覆蓋同名，但預設 server（crypto-trader）保留。

    對應 design §9.5：避免加入 Manifund 時意外移除既有 crypto-trader。
    """
    monkeypatch.delenv("MCP_EXTRA_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    custom = '{"myserver": {"command": "python", "args": ["-m", "srv"], "transport": "stdio"}}'
    monkeypatch.setenv("MCP_SERVERS", custom)
    servers = _resolve_servers()
    assert "myserver" in servers
    assert "crypto-trader" in servers  # merge，不是整份覆蓋


def test_custom_servers_null_removes_default(monkeypatch):
    """MCP_SERVERS 傳 null 可明確移除預設 server（保留退出路徑）。"""
    monkeypatch.delenv("MCP_EXTRA_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    monkeypatch.setenv("MCP_SERVERS", '{"crypto-trader": null}')
    assert "crypto-trader" not in _resolve_servers()


def test_extra_servers_append(monkeypatch):
    """MCP_EXTRA_SERVERS 只附加新 server。"""
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    monkeypatch.setenv(
        "MCP_EXTRA_SERVERS",
        '{"extra-srv": {"transport": "streamable_http", "url": "https://x.example/mcp"}}',
    )
    servers = _resolve_servers()
    assert "extra-srv" in servers
    assert "crypto-trader" in servers


def test_invalid_mcp_servers_json_falls_back(monkeypatch):
    monkeypatch.delenv("MCP_EXTRA_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    monkeypatch.setenv("MCP_SERVERS", "not-json-{{")
    servers = _resolve_servers()
    assert "crypto-trader" in servers


def test_connection_spec_strips_governance_keys():
    """治理欄位不可流入 langchain-mcp-adapters 連線設定。"""
    from core.tools.mcp_loader import connection_spec

    spec = {
        "transport": "streamable_http",
        "url": "https://x.example/mcp",
        "allowed_agents": ["a"],
        "capability": "discover",
        "required_tier": "free",
        "risk_overrides": {"t": "medium"},
        "timeout_s": 20,
        "cache_ttl_s": 300,
    }
    conn = connection_spec(spec)
    assert conn == {"transport": "streamable_http", "url": "https://x.example/mcp"}


# ──────────────────────────────────────────────────────────────────────────────
# 風險覆寫（治理映射核心）
# ──────────────────────────────────────────────────────────────────────────────


def test_risk_overrides_default_empty(monkeypatch):
    monkeypatch.delenv("MCP_TOOL_RISK_OVERRIDES", raising=False)
    assert _resolve_risk_overrides() == {}


def test_risk_overrides_parsed(monkeypatch):
    monkeypatch.setenv(
        "MCP_TOOL_RISK_OVERRIDES",
        '{"get_bitcoin_price": "low", "submit_tx": "high"}',
    )
    overrides = _resolve_risk_overrides()
    assert overrides == {"get_bitcoin_price": "low", "submit_tx": "high"}


def test_risk_overrides_rejects_invalid_level(monkeypatch):
    """不合法的 risk 值應被過濾掉（只能是 low/medium/high）。"""
    monkeypatch.setenv(
        "MCP_TOOL_RISK_OVERRIDES",
        '{"good": "medium", "bad": "critical", "alsobad": 123}',
    )
    overrides = _resolve_risk_overrides()
    assert overrides == {"good": "medium"}


def test_build_entries_defaults_low():
    """MCP tool 預設 risk_level=low、tier=free、allowed_agents=[]。"""
    tool = MagicMock()
    tool.name = "mcp_tool_a"
    entries = _build_registry_entries([tool], risk_overrides={})
    assert len(entries) == 1
    _t, risk, allowed, tier = entries[0]
    assert risk == "low"
    assert allowed == []
    assert tier == "free"


def test_build_entries_applies_override():
    """覆寫要能套用到對應的 MCP tool。"""
    tool_high = MagicMock()
    tool_high.name = "dangerous_mcp"
    tool_low = MagicMock()
    tool_low.name = "safe_mcp"
    entries = _build_registry_entries(
        [tool_high, tool_low],
        risk_overrides={"dangerous_mcp": "high"},
    )
    risks = {entries[0][0].name: entries[0][1], entries[1][0].name: entries[1][1]}
    assert risks == {"dangerous_mcp": "high", "safe_mcp": "low"}


def test_build_entries_server_governance_risk_applied():
    """server 治理 risk_overrides 要生效（無 env 覆寫時）。"""
    from core.tools.mcp_loader import MCPServerGovernance

    tool = MagicMock()
    tool.name = "get_project"
    gov = MCPServerGovernance(risk_overrides={"get_project": "medium"}, required_tier="free")
    entries = _build_registry_entries([tool], risk_overrides={}, governance=gov)
    _t, risk, _allowed, tier = entries[0]
    assert risk == "medium"
    assert tier == "free"


def test_build_entries_env_risk_beats_server_governance():
    """MCP_TOOL_RISK_OVERRIDES（env）優先於 server 治理設定。"""
    from core.tools.mcp_loader import MCPServerGovernance

    tool = MagicMock()
    tool.name = "get_project"
    gov = MCPServerGovernance(risk_overrides={"get_project": "medium"})
    entries = _build_registry_entries(
        [tool], risk_overrides={"get_project": "high"}, governance=gov
    )
    assert entries[0][1] == "high"


# ──────────────────────────────────────────────────────────────────────────────
# register_mcp_risk_overrides — 單一真相來源整合
# ──────────────────────────────────────────────────────────────────────────────


def test_register_overrides_merges_into_truth_source(monkeypatch):
    """覆寫應合併進 _TOOL_RISK_LEVELS，讓 get_tool_risk_level 查得到。"""
    monkeypatch.delenv("MCP_TOOL_RISK_OVERRIDES", raising=False)
    # 確保乾淨起點
    original = dict(_TOOL_RISK_LEVELS)
    try:
        register_mcp_risk_overrides({"new_mcp_high": "high"})
        assert get_tool_risk_level("new_mcp_high") == "high"
    finally:
        # 還原（避免污染其他測試）
        _TOOL_RISK_LEVELS.clear()
        _TOOL_RISK_LEVELS.update(original)


def test_register_overrides_does_not_clobber_existing_tool(monkeypatch):
    """既有（自家）tool 的風險等級不應被 MCP 覆寫。"""
    monkeypatch.delenv("MCP_TOOL_RISK_OVERRIDES", raising=False)
    # submit_kyc_application 既有是 high
    assert get_tool_risk_level("submit_kyc_application") == "high"
    original = dict(_TOOL_RISK_LEVELS)
    try:
        # 試圖用 MCP 覆寫成 low（模擬失誤）
        register_mcp_risk_overrides({"submit_kyc_application": "low"})
        # 應維持 high（既有 tool 優先）
        assert get_tool_risk_level("submit_kyc_application") == "high"
    finally:
        _TOOL_RISK_LEVELS.clear()
        _TOOL_RISK_LEVELS.update(original)


def test_register_overrides_rejects_invalid_level():
    original = dict(_TOOL_RISK_LEVELS)
    try:
        register_mcp_risk_overrides({"bad_mcp": "critical"})
        assert "bad_mcp" not in _TOOL_RISK_LEVELS
    finally:
        _TOOL_RISK_LEVELS.clear()
        _TOOL_RISK_LEVELS.update(original)


# ──────────────────────────────────────────────────────────────────────────────
# fail-soft：載入失敗不中斷
# ──────────────────────────────────────────────────────────────────────────────


def test_load_fail_soft_on_error(monkeypatch):
    """連線 MCP server 失敗時應回空 list，不拋例外（不中斷 bootstrap）。"""
    monkeypatch.setenv("MCP_ENABLED", "1")
    with patch(
        "core.tools.mcp_loader._fetch_mcp_tools",
        side_effect=RuntimeError("connection refused"),
    ):
        result = load_mcp_tools_sync()
    assert result == []


def test_load_success_returns_entries(monkeypatch):
    """成功載入時應回 [(tool, risk, allowed, tier)] entries。"""
    monkeypatch.setenv("MCP_ENABLED", "1")
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    monkeypatch.delenv("MCP_EXTRA_SERVERS", raising=False)
    monkeypatch.delenv("MANIFUND_MCP_ENABLED", raising=False)
    tool = MagicMock()
    tool.name = "get_crypto_price"
    tool.description = "get price"
    with patch(
        "core.tools.mcp_loader._fetch_mcp_tools",
        return_value=[tool],
    ):
        entries = load_mcp_tools_sync()
    assert len(entries) == 1
    mcp_tool, risk, allowed, tier = entries[0]
    assert mcp_tool.name == "get_crypto_price"
    assert risk == "low"  # 預設
    assert allowed == []  # unrestricted
    assert tier == "free"
