"""官方 Agent Profile catalog（版本化、唯讀）。

對應 design.md §6.1／§11.1 與 impl plan Task A1：
- 官方 Profiles 以版本化 YAML 管理（core/agents/profiles/*.yaml），不由使用者修改。
- ``CATALOG_VERSION`` 隨 catalog 內容變更遞增；preset 建立時快照當下版本。
- ``CAPABILITY_TOOL_MAP`` 定義 capability → MCP 工具名的對應（native 工具由
  ``allowed_tool_categories`` 治理，不經 capability map）。

Fail-soft 語意：單一 YAML 壞檔只剔除該 Profile（記 warning）；連
general_research.yaml 都不可用時退回內建最小定義，其餘 Profile 不曝光
（fail-closed，不擴大工具池）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

CATALOG_VERSION = "2026.09.0"
_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

# capability → MCP 工具名（impl plan Task D2 對照表）。
# advanced_funds 預設關閉：進階資金檢視（餘額／交易）需 Premium + 使用者明確開啟。
CAPABILITY_TOOL_MAP: Dict[str, Tuple[str, ...]] = {
    "discover": ("search_projects", "recommend_projects", "list_causes"),
    "project_detail": ("get_project",),
    "comments_summary": ("get_comments",),
    "person_lookup": ("search_users", "get_user"),
    "advanced_funds": ("get_user_balances", "get_txns"),
}

# 預設關閉的 capability（唯讀 Pilot 第一版）。
DEFAULT_DISABLED_CAPABILITIES: Tuple[str, ...] = ("advanced_funds",)

# 內建 fallback：catalog 全壞時至少保住 General Research（fail-closed）。
_FALLBACK_PROFILE_RAW = {
    "id": "general_research",
    "version": "0.0.0-fallback",
    "display_name": "General Research",
    "description": "一般研究、網頁搜尋、比較與摘要（fallback）",
    "tier": "free",
    "capabilities": ["web_research", "comparison", "summary"],
    "default_analysis_mode": "quick",
    "default_action_policy": "read_only",
    "allowed_data_sources": ["web"],
    "allowed_tool_categories": ["general", "news"],
    # Mixer Step 2：必要工具保護（fallback 也要有，否則 catalog 全壞時
    # 一般研究連搜尋都能被使用者勾選關掉）。
    "required_tools": ["web_search"],
    "default_skills": [],
}

VALID_MODES = ("single", "auto", "team")
VALID_ANALYSIS_MODES = ("quick", "verified", "research")
VALID_ACTION_POLICIES = ("read_only", "confirm_actions")


@dataclass(frozen=True)
class AgentProfile:
    """官方 Agent Profile（不可變）。"""

    id: str
    version: str
    display_name: str
    description: str
    tier: str = "free"
    capabilities: Tuple[str, ...] = ()
    default_analysis_mode: str = "quick"
    default_action_policy: str = "read_only"
    allowed_data_sources: Tuple[str, ...] = ()
    allowed_tool_categories: Tuple[str, ...] = ()
    # Mixer Step 2：必要工具——使用者 per-agent 勾選不可取消（resolver
    # 第 8 層會在交集後回補）。拿掉就失去該 agent 存在意義的最小集。
    required_tools: Tuple[str, ...] = ()
    default_skills: Tuple[str, ...] = ()

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "tier": self.tier,
            "capabilities": list(self.capabilities),
            "default_analysis_mode": self.default_analysis_mode,
            "default_action_policy": self.default_action_policy,
            "allowed_data_sources": list(self.allowed_data_sources),
            "allowed_tool_categories": list(self.allowed_tool_categories),
            "required_tools": list(self.required_tools),
            "default_skills": list(self.default_skills),
        }


def _coerce_profile(raw: dict) -> AgentProfile:
    """把 YAML dict 正規化為 AgentProfile（未知欄位忽略、清單轉 tuple）。"""
    return AgentProfile(
        id=str(raw["id"]),
        version=str(raw.get("version", "0.0.0")),
        display_name=str(raw.get("display_name", raw["id"])),
        description=str(raw.get("description", "")),
        tier=str(raw.get("tier", "free")),
        capabilities=tuple(str(c) for c in raw.get("capabilities", [])),
        default_analysis_mode=str(raw.get("default_analysis_mode", "quick")),
        default_action_policy=str(raw.get("default_action_policy", "read_only")),
        allowed_data_sources=tuple(str(s) for s in raw.get("allowed_data_sources", [])),
        allowed_tool_categories=tuple(
            str(c) for c in raw.get("allowed_tool_categories", [])
        ),
        required_tools=tuple(str(t) for t in raw.get("required_tools", [])),
        default_skills=tuple(str(s) for s in raw.get("default_skills", [])),
    )


class ProfileCatalog:
    """唯讀 catalog：load 一次、process 生命周期內快取（比照 SkillLoader）。"""

    def __init__(self, profiles: Dict[str, AgentProfile]):
        self._profiles: Dict[str, AgentProfile] = dict(profiles)

    def get(self, profile_id: str) -> Optional[AgentProfile]:
        return self._profiles.get(profile_id)

    def require(self, profile_id: str) -> AgentProfile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"unknown agent profile: {profile_id}")
        return profile

    def list_profiles(self) -> List[AgentProfile]:
        return [self._profiles[k] for k in sorted(self._profiles)]

    def validate_agent_ids(self, agent_ids: List[str]) -> Tuple[List[str], List[str]]:
        """Split ids into (valid, unknown)。重複 id 去重（保序）。"""
        seen = set()
        valid: List[str] = []
        unknown: List[str] = []
        for aid in agent_ids:
            if aid in seen:
                continue
            seen.add(aid)
            if aid in self._profiles:
                valid.append(aid)
            else:
                unknown.append(aid)
        return valid, unknown

    def all_capabilities(self) -> set:
        caps = set()
        for p in self._profiles.values():
            caps.update(p.capabilities)
        caps.update(CAPABILITY_TOOL_MAP.keys())
        return caps

    def tools_for_capabilities(self, capabilities) -> set:
        """capability 集合 → MCP 工具名集合。"""
        names: set = set()
        for cap in capabilities:
            names.update(CAPABILITY_TOOL_MAP.get(cap, ()))
        return names


def load_catalog(profiles_dir: Optional[Path] = None) -> ProfileCatalog:
    """從 YAML 目錄載入 catalog；單檔壞 → 剔除該檔；全壞 → fallback。"""
    directory = Path(profiles_dir) if profiles_dir else _PROFILES_DIR
    loaded: Dict[str, AgentProfile] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or "id" not in raw:
                    raise ValueError("profile YAML must be a mapping with an 'id'")
                profile = _coerce_profile(raw)
                if profile.id in loaded:
                    raise ValueError(f"duplicate profile id: {profile.id}")
                loaded[profile.id] = profile
            except (OSError, yaml.YAMLError, ValueError) as exc:
                logger.warning("[profiles] skipping invalid profile %s: %s", path, exc)
    if "general_research" not in loaded:
        logger.warning("[profiles] general_research missing; using built-in fallback")
        loaded["general_research"] = _coerce_profile(dict(_FALLBACK_PROFILE_RAW))
    return ProfileCatalog(loaded)


_catalog: Optional[ProfileCatalog] = None
_catalog_lock = threading.Lock()


def get_profile_catalog() -> ProfileCatalog:
    """Process-level singleton（啟動載入一次，不隨請求重載）。"""
    global _catalog
    if _catalog is None:
        with _catalog_lock:
            if _catalog is None:
                _catalog = load_catalog()
    return _catalog


def reset_profile_catalog_for_tests(profiles_dir: Optional[Path] = None) -> None:
    """測試輔助：重置 singleton。"""
    global _catalog
    with _catalog_lock:
        _catalog = load_catalog(profiles_dir)
