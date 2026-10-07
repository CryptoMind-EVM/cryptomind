"""
Manager Agent — 多 Agent 協調中心

核心功能：
1. 開放式意圖理解 - 不使用硬編碼類別/關鍵字
2. Vending vs Restaurant 模式 - 簡單任務快速路由
3. DAG 執行引擎 - 支援垂直/水平任務
4. 選擇性上下文傳輸 - Sub-Agent 只接收必要資訊
5. 短期記憶整合 - 對話上下文管理
6. 長期記憶整合 - 持久化用戶偏好與歷史

This module is a facade that re-exports from the manager package.
All implementation lives in core/agents/manager/ submodules.
"""

from core.agents.context_budget import (  # noqa: F401
    CONTEXT_CHAR_BUDGET,
    format_compact_state,
    history_exceeds_budget,
)
from core.agents.manager._main import (  # noqa: F401
    AGENT_EXECUTION_TIMEOUT,
    MANAGER_GRAPH_RECURSION_LIMIT,
    MAX_GRAPH_TASKS,
    MEMORY_CONSOLIDATION_THRESHOLD,
    MEMORY_IDLE_TIMEOUT,
    ManagerAgent,
    _background_tasks,
    _experience_store,
    _extract_model_name_for_manager,
    _get_checkpointer,
    _get_history_for_prompt,
    _per_user_checkpointer,
    _read_compact_for_manager,
    _run_background,
    _TracedGraph,
)
from core.agents.models import CLEAR_SENTINEL  # noqa: F401

__all__ = [
    "CLEAR_SENTINEL",
    "ManagerAgent",
    "MANAGER_GRAPH_RECURSION_LIMIT",
    "MAX_GRAPH_TASKS",
    "MEMORY_CONSOLIDATION_THRESHOLD",
    "MEMORY_IDLE_TIMEOUT",
    "AGENT_EXECUTION_TIMEOUT",
    "_TracedGraph",
    "_extract_model_name_for_manager",
    "_get_checkpointer",
    "_get_history_for_prompt",
    "_per_user_checkpointer",
    "_read_compact_for_manager",
    "_run_background",
    "_background_tasks",
    "_experience_store",
]
