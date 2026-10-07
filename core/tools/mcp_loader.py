"""MCP client loader — 把外部 MCP server 的 tools 載入 CryptoMind 的 tool registry。

可信 AI / 資訊搜集增強：讓 CLAW agent 能呼叫開源 MCP server 提供的資料源
（行情、鏈上、新聞、Manifund 等），不必每接一個資料源就手寫 client。

設計重點（對應 docs/MCP_INTEGRATION_ASSESSMENT.md 與
docs/plans/2026-08-14-general-agent-platform-ai-studio-manifund-impl.md Task A3）：
- **預設關閉**：MCP_ENABLED 未設時完全不載入，不影響既有流程、不強迫依賴。
- **merge 語意**：MCP_SERVERS 只覆蓋同名 server（傳 null 可明確移除預設 server），
  MCP_EXTRA_SERVERS 只附加——避免加入新 server 時意外移除既有 crypto-trader。
- **per-server 治理 metadata**：每個 server spec 可帶治理欄位
  （allowed_agents/capability/required_tier/risk_overrides/timeout_s/cache_ttl_s），
  交給 langchain-mcp-adapters 前會剝除。MCP tool 預設 risk_level=low；
  **high-risk 必須顯式標記**，絕不自動給 high——避免高風險動作繞過 Consent Gate。
- **server 級開關**：manifund 預設 server 需 MANIFUND_MCP_ENABLED=1 才會參與解析。
- **fail-soft**：載入失敗只記 warning，不中斷 bootstrap。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from api.utils import logger

# 預設 MCP server 清單（MCP_ENABLED=1 時才啟用）。
# crypto-trader 是 Python/FastMCP server（CoinGecko，免 key、免 Node），
# Dockerfile 會 git clone 到容器內固定路徑。本機開發可用 MCP_CRYPTO_TRADER_PATH
# env 指向 clone 下來的 main.py 路徑。
# manifund 是官方 Streamable HTTP MCP（唯讀、免驗證）；需 MANIFUND_MCP_ENABLED=1。
_CRYPTO_TRADER_PATH = os.getenv(
    "MCP_CRYPTO_TRADER_PATH", "/app/mcp-servers/crypto-trader/main.py"
)
_DEFAULT_SERVERS: Dict[str, Dict[str, Any]] = {
    "crypto-trader": {
        "command": "python",
        "args": [_CRYPTO_TRADER_PATH],
        "transport": "stdio",
    },
    "manifund": {
        "transport": "streamable_http",
        "url": "https://manifund.org/api/mcp",
        # ── 以下為治理欄（交給 MCP client 前會剝除）──
        # Profile 層級的工具池治理由 capability resolver 負責（capability →
        # 工具名對應），此處 capability 僅作為標示。advanced_funds（餘額／交易）
        # 在 resolver 預設關閉、tier=premium，見 impl plan Task D2。
        "allowed_agents": [],
        "capability": "discover",
        "required_tier": "free",
        "risk_overrides": {
            "search_projects": "medium",
            "recommend_projects": "medium",
            "get_project": "medium",
            "get_comments": "medium",
            "search_users": "medium",
            "get_user": "medium",
            "get_user_balances": "medium",
            "get_txns": "medium",
        },
        "timeout_s": 20,
        "cache_ttl_s": 300,
    },
}

# MCP tool 預設綁定：allowed_agents（[] = 所有 agent 可用）、required_tier。
_MCP_DEFAULT_ALLOWED_AGENTS: List[str] = []  # [] = unrestricted
_MCP_DEFAULT_TIER = "free"

# server spec 中的治理欄位（不可流入 MCP client 連線設定）。
_GOVERNANCE_KEYS = (
    "allowed_agents",
    "capability",
    "required_tier",
    "risk_overrides",
    "timeout_s",
    "cache_ttl_s",
)

_VALID_RISK_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True)
class MCPServerGovernance:
    """單一 MCP server 的治理設定（風險/tier/capability 標示）。"""

    allowed_agents: Tuple[str, ...] = ()
    capability: Optional[str] = None
    required_tier: str = _MCP_DEFAULT_TIER
    risk_overrides: Dict[str, str] = field(default_factory=dict)
    timeout_s: float = 30.0
    cache_ttl_s: int = 0


def _parse_governance(spec: Dict[str, Any]) -> MCPServerGovernance:
    raw_risk = spec.get("risk_overrides") or {}
    risk_overrides = {
        str(k): str(v)
        for k, v in raw_risk.items()
        if isinstance(v, str) and v in _VALID_RISK_LEVELS
    }
    return MCPServerGovernance(
        allowed_agents=tuple(str(a) for a in spec.get("allowed_agents") or ()),
        capability=spec.get("capability"),
        required_tier=str(spec.get("required_tier", _MCP_DEFAULT_TIER)),
        risk_overrides=risk_overrides,
        timeout_s=float(spec.get("timeout_s", 30.0)),
        cache_ttl_s=int(spec.get("cache_ttl_s", 0)),
    )


def connection_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """剝除治理欄位，回傳純連線設定（給 langchain-mcp-adapters）。"""
    return {k: v for k, v in spec.items() if k not in _GOVERNANCE_KEYS}


def _server_enabled(name: str) -> bool:
    """server 級開關：manifund 需 MANIFUND_MCP_ENABLED=1。"""
    if name == "manifund":
        from core.feature_flags import manifund_mcp_enabled

        return manifund_mcp_enabled()
    return True


def _resolve_servers() -> Dict[str, Dict[str, Any]]:
    """解析最終 server 清單（merge 語意）。

    - 起點：_DEFAULT_SERVERS（經 server 級開關過濾）。
    - MCP_SERVERS：覆蓋同名 server；值為 null 表示明確移除該預設 server。
    - MCP_EXTRA_SERVERS：只附加（同名覆蓋，記 warning）。
    """
    servers: Dict[str, Dict[str, Any]] = {
        name: dict(spec) for name, spec in _DEFAULT_SERVERS.items()
    }

    custom = os.getenv("MCP_SERVERS")
    if custom:
        try:
            parsed = json.loads(custom)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    name = str(key)
                    if value is None:
                        servers.pop(name, None)
                    elif isinstance(value, dict):
                        servers[name] = value
            else:
                logger.warning("[MCP] MCP_SERVERS must be a JSON object; ignoring")
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                f"[MCP] failed to parse MCP_SERVERS JSON, falling back to defaults: {exc}"
            )

    extra = os.getenv("MCP_EXTRA_SERVERS")
    if extra:
        try:
            parsed = json.loads(extra)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    name = str(key)
                    if not isinstance(value, dict):
                        logger.warning("[MCP] MCP_EXTRA_SERVERS entry %s ignored (not an object)", name)
                        continue
                    if name in servers:
                        logger.warning("[MCP] MCP_EXTRA_SERVERS overrides existing server %s", name)
                    servers[name] = value
            else:
                logger.warning("[MCP] MCP_EXTRA_SERVERS must be a JSON object; ignoring")
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"[MCP] failed to parse MCP_EXTRA_SERVERS JSON: {exc}")

    return {name: spec for name, spec in servers.items() if _server_enabled(name)}


def _resolve_risk_overrides() -> Dict[str, str]:
    """從 MCP_TOOL_RISK_OVERRIDES env（JSON）讀風險覆寫（全域，最高優先）。"""
    raw = os.getenv("MCP_TOOL_RISK_OVERRIDES")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # 只接受合法值
            return {
                str(k): v
                for k, v in parsed.items()
                if v in _VALID_RISK_LEVELS
            }
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(f"[MCP] failed to parse MCP_TOOL_RISK_OVERRIDES JSON: {exc}")
    return {}


def _is_enabled() -> bool:
    from core.feature_flags import env_flag

    return env_flag("MCP_ENABLED")


async def _fetch_mcp_tools(servers: Dict[str, Dict[str, Any]]) -> List[Any]:
    """連線 MCP server 並取回 BaseTool list（治理欄位會先剝除）。"""
    # 延遲 import：MCP_ENABLED=0 時不強迫 langchain_mcp_adapters 依賴存在
    from langchain_mcp_adapters.client import MultiServerMCPClient

    connections = {name: connection_spec(spec) for name, spec in servers.items()}
    client = MultiServerMCPClient(connections=connections)
    tools = await client.get_tools()
    return list(tools)


def _build_registry_entries(
    tools: List[Any],
    risk_overrides: Dict[str, str],
    governance: Optional[MCPServerGovernance] = None,
) -> List[Tuple[Any, str, List[str], str]]:
    """把 MCP BaseTool 轉成 (tool, risk_level, allowed_agents, required_tier)。

    risk 優先序：MCP_TOOL_RISK_OVERRIDES（env，全域）> server 治理
    risk_overrides > "low"。high 必須顯式標記，絕不自動升 high。
    """
    gov = governance or MCPServerGovernance()
    entries = []
    for t in tools:
        name = getattr(t, "name", None) or str(t)
        risk = risk_overrides.get(name) or gov.risk_overrides.get(name, "low")
        if risk not in _VALID_RISK_LEVELS:
            risk = "low"
        entries.append((t, risk, list(gov.allowed_agents), gov.required_tier))
    return entries


def load_mcp_tools_sync() -> List[Tuple[Any, str, List[str], str]]:
    """同步載入 MCP tools（供 bootstrap 同步路徑呼叫）。

    回傳 [(BaseTool, risk_level, allowed_agents, required_tier), ...]。
    MCP_ENABLED=0 或載入失敗時回傳空 list（fail-soft，不中斷 bootstrap）。
    逐 server 連線以保留治理 attribution。
    """
    if not _is_enabled():
        # 顯式記錄 env 值，讓「env 沒注入到 runtime」能被 log 看見
        # （Zeabur 常見：設在 project variables 但沒綁到該 service）。
        logger.info(
            "[MCP] disabled (MCP_ENABLED=%r). Set MCP_ENABLED=1 to enable.",
            os.getenv("MCP_ENABLED"),
        )
        return []

    servers = _resolve_servers()
    env_risk_overrides = _resolve_risk_overrides()
    entries: List[Tuple[Any, str, List[str], str]] = []

    for server_name, spec in servers.items():
        gov = _parse_governance(spec)
        try:
            tools = asyncio.run(_fetch_mcp_tools({server_name: spec}))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.warning(
                f"[MCP] failed to load tools from server '{server_name}' "
                f"(fail-soft, skipping): {exc}"
            )
            continue
        entries.extend(_build_registry_entries(tools, env_risk_overrides, gov))

    if servers:
        logger.info(
            f"[MCP] loaded {len(entries)} MCP tools: "
            f"{[getattr(t, 'name', '?') for t, _, _, _ in entries]}"
        )
    return entries
