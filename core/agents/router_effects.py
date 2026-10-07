"""Router 決策的下游效果（2026-09-12 DANNY 拍板）：控推理預算與工具池，不是換模型。

背景：ModelRouter 的 task_type → 模型分流對 BYOK 使用者是死的（他們只有自家
模型；#665 量過換模型本身沒價值，有效的是推理預算）。Router 已經會產出
``depth``（lookup／analysis／research）與 ``domains``（profile id），這裡把它們
接到兩個真正影響延遲與正確率的旋鈕：

1. **推理預算**：``ROUTER_DEPTH_EFFORT_LOOKUP`` / ``_ANALYSIS`` / ``_RESEARCH``
   （如 low／medium／high；空＝不動）。經 ``ReasoningBudgetMiddleware`` 寫進
   ``model_settings["reasoning_effort"]``——跟 phased_model 同一個機制。
2. **工具池**：domains 對應的 profile 允許的工具類別（＋``general`` 系統工具、
   profile 的 required_tools、MCP capability 工具）做交集；只能縮小，不能解鎖。
   depth=research 不縮（開放式問題不該先把工具拿掉）。
   ``ROUTER_TOOL_POOL_NARROWING=false`` 關掉。

兩者都只在 Router 真的跑過（source=router／t0_cache）時生效；fallback／veto
沒有 domains，等於不動。預設值刻意保守（推理預算全空），要開請先跑
``scripts/eval_claw.py --live`` 當閘門。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

_EFFORT_ENV = {
    "lookup": "ROUTER_DEPTH_EFFORT_LOOKUP",
    "analysis": "ROUTER_DEPTH_EFFORT_ANALYSIS",
    "research": "ROUTER_DEPTH_EFFORT_RESEARCH",
}
_VALID_EFFORTS = {"minimal", "low", "medium", "high"}
_SYSTEM_CATEGORY = "general"


def reasoning_effort_for_depth(depth: Optional[str]) -> str:
    """depth → reasoning_effort；沒設或不合法一律空字串（不動）。"""
    env_name = _EFFORT_ENV.get((depth or "").strip().lower())
    if not env_name:
        return ""
    value = os.getenv(env_name, "").strip().lower()
    return value if value in _VALID_EFFORTS else ""


def tool_pool_narrowing_enabled() -> bool:
    return os.getenv("ROUTER_TOOL_POOL_NARROWING", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def router_tool_names(
    domains: Iterable[str], depth: Optional[str], catalog: Any = None
) -> Optional[list[str]]:
    """domains → 允許的工具名清單；None＝不縮。"""
    if not tool_pool_narrowing_enabled():
        return None
    domain_ids = [str(d).strip() for d in (domains or []) if str(d).strip()]
    if not domain_ids or (depth or "").strip().lower() == "research":
        return None
    from core.agents.capability_resolver import build_category_lookup
    from core.agents.profile_catalog import get_profile_catalog

    cat = catalog or get_profile_catalog()
    allowed_categories: set[str] = {_SYSTEM_CATEGORY}
    names: set[str] = set()
    matched = 0
    for pid in domain_ids:
        profile = cat.get(pid)
        if profile is None:
            continue
        matched += 1
        allowed_categories.update(profile.allowed_tool_categories)
        names.update(getattr(profile, "required_tools", ()) or ())
        try:
            names.update(cat.tools_for_capabilities(profile.capabilities))
        except Exception:  # noqa: BLE001 — MCP 目錄缺席不影響縮池
            pass
    if not matched:
        return None
    for tool_name, category in build_category_lookup().items():
        if category in allowed_categories:
            names.add(tool_name)
    return sorted(names)


def derive_router_effects(route_decision: Optional[dict]) -> dict:
    """RouteDecision.metrics_dict() → SubTask context 要帶的兩個鍵。"""
    effects: dict = {"router_tool_names": None, "reasoning_effort": ""}
    if not isinstance(route_decision, dict):
        return effects
    if route_decision.get("source") not in ("router", "t0_cache"):
        return effects
    depth = route_decision.get("depth")
    domains_raw = route_decision.get("domains") or ""
    domains = [d for d in str(domains_raw).split(",") if d]
    effects["reasoning_effort"] = reasoning_effort_for_depth(depth)
    effects["router_tool_names"] = router_tool_names(domains, depth)
    if effects["router_tool_names"] is not None:
        logger.info(
            "[Router] tool pool narrowed to %d tools (domains=%s depth=%s)",
            len(effects["router_tool_names"]),
            ",".join(domains),
            depth,
        )
    return effects


try:  # langchain 的 middleware 基底；測試環境沒裝時退回純物件
    from langchain.agents.middleware import AgentMiddleware as _Base
except Exception:  # noqa: BLE001
    _Base = object  # type: ignore[assignment,misc]


class ReasoningBudgetMiddleware(_Base):  # type: ignore[misc]
    """把 reasoning_effort 疊進每次 model call 的 model_settings。"""

    def __init__(self, effort: str) -> None:
        if _Base is not object:
            super().__init__()
        self.effort = effort

    def _request(self, request: Any) -> Any:
        settings = {
            **(getattr(request, "model_settings", None) or {}),
            "reasoning_effort": self.effort,
        }
        return request.override(model_settings=settings)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._request(request))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._request(request))
