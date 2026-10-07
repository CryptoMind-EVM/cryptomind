"""
Agent Models — Shared data structures

包含：
- 基礎類別（TaskComplexity, CollaborationRequest, AgentResult, SubTask）
- 核心類別（ExecutionMode, TaskNode, TaskGraph, ManagerState 等）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

# ============================================================================
# Structured Output Schemas（LangChain with_structured_output）
# ============================================================================


class IntentTask(BaseModel):
    """Task decomposition from intent understanding."""

    id: str = Field(description="Task identifier, format: task_N")
    name: str = Field(description="Short task name")
    agent: str = Field(
        description="Agent type: crypto, tw_stock, us_stock, news, sentiment, technical, general"
    )
    objective: str = Field(description="What this task should accomplish")
    data_needs: list[str] = Field(
        default_factory=list, description="Data dimensions needed"
    )
    output_format: str = Field(default="", description="Expected output format")
    boundaries: str = Field(default="", description="Clear scope boundaries")
    description: str = Field(description="Full task instruction text")
    dependencies: list[str] = Field(
        default_factory=list, description="Task IDs this depends on"
    )


class IntentUnderstandingStructured(BaseModel):
    """Structured output schema for intent understanding LLM responses."""

    status: Literal["ready", "clarify"] = Field(
        description="Intent resolution status"
    )
    user_intent: str = Field(default="", description="Core user intent")
    entities: dict = Field(
        default_factory=dict, description="Extracted entities: symbol, market"
    )
    clarification_question: str | None = Field(
        default=None, description="Question to ask if clarification needed"
    )
    tasks: list[IntentTask] = Field(
        default_factory=list, description="Decomposed tasks"
    )
    aggregation_strategy: Literal["combine_all", "last_only", "hierarchical"] = Field(
        default="combine_all"
    )


class ReflectionIssue(BaseModel):
    """Single issue found during result reflection."""

    task_id: str = Field(description="Task identifier with issue")
    agent: str = Field(description="Agent type")
    problem: str = Field(description="Problem description")
    action: Literal["retry", "remove", "keep"] = Field(description="Corrective action")


class ReflectionResultStructured(BaseModel):
    """Structured output schema for reflection LLM responses."""

    issues: list[ReflectionIssue] = Field(
        default_factory=list, description="Issues found during review"
    )
    needs_retry: bool = Field(default=False, description="Whether any task needs retry")
    tasks_to_retry: list[str] = Field(
        default_factory=list, description="Task IDs to retry"
    )
    retry_hints: dict = Field(
        default_factory=dict, description="Hints for retry: task_id -> hint text"
    )
    cleaned_results: dict | None = Field(
        default=None, description="Cleaned results per task"
    )
    summary: str = Field(default="", description="Overall quality assessment")


# ============================================================================
# Entity Resolution Schema（業界標準 Structured Output）
# ============================================================================


class ExtractedEntity(BaseModel):
    """
    LLM 用 Structured Output 抽取的資產實體。

    這個 schema 取代舊的 regex token 切分 + hardcoded 字典做法：
    - LLM 做 NLU（中文公司名、英文 ticker、暱稱識別）
    - 後續 symbol_normalizer 工具用 API 確認標準 ticker
    """

    market: Literal[
        "crypto",
        "tw",
        "us",
        "hk",
        "jp",
        "kr",
        "in",
        "cn",
        "forex",
        "commodity",
        "unknown",
    ] = Field(description="標的所屬市場；無法判斷時填 unknown")

    asset_name: str = Field(
        description=(
            "用戶在問題中使用的名稱（可能是中文公司名如「台積電」、"
            "英文 ticker 如「AAPL」、暱稱如「派幣」）"
        )
    )

    candidate_symbol: Optional[str] = Field(
        default=None,
        description=(
            "LLM 對標準代號的猜測（會被 symbol_normalizer 用 API 驗證）。"
            "tw: 數字代號如 2330；us: ticker 如 AAPL；crypto: 大寫如 BTC；"
            "hk: 數字代號如 0700；jp: 數字+T 如 7203；kr: 數字+KS 如 005930。"
            "不確定就留 null。"
        ),
    )

    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="LLM 對此識別的信心 0-1，影響是否走 fallback",
    )

    reasoning: str = Field(
        default="",
        description="為什麼這樣判斷（debug 用，可選）",
    )


class ExtractedEntityList(BaseModel):
    """
    一個 query 可能包含多個 entity（如「比較 BTC 跟 ETH」）。
    """

    entities: List[ExtractedEntity] = Field(
        default_factory=list,
        description="從 query 抽取的所有資產實體；query 沒提到任何資產時為空 list",
    )


# ============================================================================
# 基礎類別
# ============================================================================


class TaskComplexity(Enum):
    """任務複雜度"""

    SIMPLE = "simple"
    COMPLEX = "complex"
    AMBIGUOUS = "ambiguous"


@dataclass
class CollaborationRequest:
    """協作請求（基礎）"""

    requesting_agent: str
    needed_agent: str
    context: str
    priority: Literal["required", "optional"]


@dataclass
class AgentResult:
    """Agent 執行結果（基礎）"""

    success: bool
    message: str
    agent_name: str
    data: dict = field(default_factory=dict)
    quality: Literal["pass", "fail"] = "pass"
    quality_fail_reason: Optional[str] = None
    needs_collaboration: Optional[CollaborationRequest] = None


@dataclass
class SubTask:
    """子任務（基礎）"""

    step: int
    description: str
    agent: str
    tool_hint: Optional[str] = None
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    result: Optional[AgentResult] = None
    context: dict = field(default_factory=dict)


# ============================================================================
# 自定義 Reducer（用於 LangGraph 狀態合併）
# ============================================================================

CLEAR_SENTINEL = "__CLEAR__"  # 清除信號


def task_results_reducer(left: Dict | None, right: Dict | None) -> Dict:
    """
    自定義 task_results reducer

    行為：
    - 如果 right 包含 CLEAR_SENTINEL key → 完全替換為 right（清除舊值）
    - 否則 → 合併 left 和 right（預設行為）
    """
    if right is None:
        return left or {}
    if left is None:
        left = {}
    # 檢查清除信號
    if CLEAR_SENTINEL in right:
        # 移除信號後返回新的 dict（完全替換）
        new_dict = {k: v for k, v in right.items() if k != CLEAR_SENTINEL}
        return new_dict
    # 正常合併
    return {**left, **right}


def last_value_reducer(left: Any, right: Any) -> Any:
    """Last-write-wins reducer（右邊覆蓋左邊）。

    用於 session_id / query 等純量欄位。沒有 reducer 時，LangGraph 對同一 step
    內多次寫入同一 channel 會拋 INVALID_CONCURRENT_GRAPH_UPDATE（多標的/連續
    對話失敗後 resume 時易觸發）。加此 reducer 讓並發寫入取最後值，不崩潰。
    """
    return right if right is not None else left


def replace_optional_value(left: Any, right: Any) -> Any:
    """Explicit None clears per-turn configuration; an omitted update retains it."""
    return right


# ============================================================================
# 執行模式
# ============================================================================


class ExecutionMode(Enum):
    """任務執行模式"""

    VENDING = "vending"  # 簡單任務：直接路由，無需複雜規劃
    RESTAURANT = "restaurant"  # 複雜任務：需要規劃、拆解、DAG 執行


class HITLType(Enum):
    """
    Human-in-the-Loop 類型

    簡化設計：
    - 只保留 CONFIRM_PLAN（計劃確認）
    - 其他互動透過自然對話處理
    """

    CONFIRM_PLAN = "confirm_plan"  # 確認計劃：展示計劃請用戶確認


# ============================================================================
# 任務節點（DAG 核心）
# ============================================================================


@dataclass
class TaskNode:
    """
    任務節點 - DAG 的基本單元（Anthropic Orchestrator-Worker pattern）

    可以是：
    - task: 單一任務（指派給某個 agent）
    - group: 任務組（包含多個 children）

    每個 task 必須含 4 元素（Anthropic best practice）：
      objective / data_needs / output_format / boundaries
    這 4 項組合成 description 傳給 agent，agent 就能對照工具描述挑工具。
    """

    id: str  # 唯一標識
    name: str  # 任務名稱
    type: Literal["task", "group"]  # 節點類型

    # task 專屬
    agent: Optional[str] = None  # 指派的 agent
    tool_hint: Optional[str] = None  # 工具提示（非強制，向後相容）
    description: Optional[str] = None  # 任務完整指示文字（傳給 agent）

    # 結構化任務規格（Anthropic 4 元素）
    objective: Optional[str] = None  # 這個 task 要達成什麼
    data_needs: List[str] = field(
        default_factory=list
    )  # 資料維度清單（語意名，非工具名）
    output_format: Optional[str] = None  # 預期輸出形式
    boundaries: Optional[str] = None  # 任務界線

    # group 專屬
    children: List[TaskNode] = field(default_factory=list)

    # 執行控制
    dependencies: List[str] = field(default_factory=list)  # 依賴的任務 ID
    parallel_group: Optional[str] = None  # 並行組標識（相同標識並行執行）
    execution_strategy: Literal["sequential", "parallel", "auto"] = "auto"
    result_strategy: Literal["last_only", "combine_all", "custom"] = "last_only"

    # 執行狀態
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    result: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """驗證節點有效性"""
        if self.type == "task" and not self.agent:
            raise ValueError(f"Task node '{self.id}' must have an agent")
        if self.type == "group" and not self.children:
            raise ValueError(f"Group node '{self.id}' must have children")


@dataclass
class TaskGraph:
    """
    任務圖 - DAG 結構

    負責：
    - 管理所有任務節點
    - 拓撲排序
    - 偵測並行組
    """

    root: TaskNode
    all_nodes: Dict[str, TaskNode] = field(default_factory=dict)

    def __post_init__(self):
        """建立節點索引"""
        self._build_index(self.root)

    def _build_index(self, node: TaskNode):
        """遞迴建立節點索引"""
        self.all_nodes[node.id] = node
        for child in node.children:
            self._build_index(child)

    def get_execution_order(self) -> List[List[TaskNode]]:
        """
        取得執行順序（拓撲排序 + 並行分組）

        Returns:
            List[List[TaskNode]] - 每個內層 List 是可並行執行的任務
        """
        # 計算每個節點的入度
        in_degree = {nid: 0 for nid in self.all_nodes}
        for node in self.all_nodes.values():
            for dep_id in node.dependencies:
                if dep_id in in_degree:
                    in_degree[node.id] += 1

        # BFS 拓撲排序
        result = []
        queue = [nid for nid, deg in in_degree.items() if deg == 0]

        while queue:
            # 當前層（無依賴的節點）可並行
            current_level = [self.all_nodes[nid] for nid in queue]
            result.append(current_level)

            # 更新入度
            next_queue = []
            for nid in queue:
                node = self.all_nodes[nid]
                # 找到依賴當前節點的節點
                for other in self.all_nodes.values():
                    if nid in other.dependencies:
                        in_degree[other.id] -= 1
                        if in_degree[other.id] == 0:
                            next_queue.append(other.id)
            queue = next_queue

        return result

    def get_parallel_groups(self) -> Dict[str, List[TaskNode]]:
        """取得所有並行組"""
        groups: Dict[str, List[TaskNode]] = {}
        for node in self.all_nodes.values():
            if node.parallel_group:
                if node.parallel_group not in groups:
                    groups[node.parallel_group] = []
                groups[node.parallel_group].append(node)
        return groups


# ============================================================================
# Agent 上下文（選擇性傳輸）
# ============================================================================


@dataclass
class AgentContext:
    """
    Sub-Agent 接收的上下文

    設計原則：選擇性傳輸
    - 必帶：任務相關資訊
    - 動態帶：有依賴時帶依賴結果
    - 摘要帶：歷史壓縮
    - 不帶：完整歷史
    """

    # === 必帶 ===
    original_query: str  # 原始用戶問題
    task_description: str  # 經理指派的具體任務
    symbols: Dict[str, str]  # 萃取的實體（如 BTC → bitcoin）
    system_prompt: Optional[str] = None  # 使用者自訂 system prompt
    enabled_tools: List[str] = field(default_factory=list)  # 使用者啟用的工具

    # === Anthropic 4 元素（task 結構化規格）===
    objective: Optional[str] = None
    data_needs: List[str] = field(default_factory=list)
    output_format: Optional[str] = None
    boundaries: Optional[str] = None

    # === 動態帶（有依賴時）===
    dependency_results: Dict[str, Any] = field(default_factory=dict)
    allowed_tools: List[str] = field(default_factory=list)

    # === 摘要帶（有歷史時）===
    history_summary: Optional[str] = None  # 壓縮後的對話歷史
    memory_context: Optional[str] = None
    experience_hint: Optional[str] = None

    # === 元數據 ===
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 短期記憶
# ============================================================================


@dataclass
class ShortTermMemory:
    """
    短期記憶 - 跨對話輪次的狀態保持（per-session，進程內）

    組成：
    - conversation_history: 完整對話歷史（consolidate 的資料源）
    - symbol_cache: 符號快取（避免重複解析；同對話 ticker→symbol 重用）

    設計變更紀錄（2026-07）：
    - 移除 facts / context_state / get_compressed_history：長期記憶層
      （user_facts 表 + MemoryStore.get_memory_context）已完全取代，短期緩衝
      無存在意義。get_compressed_history 則被 base_react_agent 的設計變更淘汰
      （history 改走標準 messages 結構）。
    - 保留 symbol_cache：唯一有短期獨有價值（同對話重用、跨對話不該記得），
      雖目前 ROI 低但留接線彈性。
    """

    MAX_CONVERSATION_LENGTH = 100

    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    symbol_cache: Dict[str, str] = field(default_factory=dict)

    def add_message(self, role: str, content: str):
        self.conversation_history.append({"role": role, "content": content})
        if len(self.conversation_history) > self.MAX_CONVERSATION_LENGTH:
            self.conversation_history = self.conversation_history[
                -self.MAX_CONVERSATION_LENGTH :
            ]

    def update_symbol(self, user_symbol: str, resolved_symbol: str):
        """更新符號快取"""
        self.symbol_cache[user_symbol.lower()] = resolved_symbol

    def get_symbol(self, user_symbol: str) -> Optional[str]:
        """從快取取得符號"""
        return self.symbol_cache.get(user_symbol.lower())


# ============================================================================
# 意圖理解結果（開放式）
# ============================================================================


@dataclass
class IntentUnderstanding:
    """
    意圖理解結果 - 開放式，無硬編碼類別

    LLM 自由描述：
    - 用戶想解決什麼問題
    - 需要什麼資訊

    注意：移除了 needs_clarification，因為澄清直接在回應中處理
    """

    # 用戶意圖描述
    user_intent: str  # 如 "想了解 BTC 的價格和技術分析"

    # 萃取的實體
    entities: Dict[str, str] = field(default_factory=dict)  # 如 {"symbol": "BTC"}

    # 執行模式
    execution_mode: ExecutionMode = ExecutionMode.VENDING

    # 推斷的 agent 偏好（可選）
    suggested_agent: Optional[str] = None

    # 信心度
    confidence: Literal["high", "medium", "low"] = "high"


# ============================================================================
# 執行結果
# ============================================================================


@dataclass
class TaskResult:
    """任務執行結果"""

    success: bool
    message: str
    agent_name: str
    task_id: str  # 對應的 TaskNode ID
    data: Dict[str, Any] = field(default_factory=dict)

    # 品質評估
    quality: Literal["pass", "fail"] = "pass"
    quality_fail_reason: Optional[str] = None


@dataclass
class ExecutionResult:
    """
    整體執行結果

    支援：
    - 單一結果（vending 模式）
    - 彙總結果（restaurant 模式）
    """

    success: bool
    final_response: str
    mode: ExecutionMode

    # 執行詳情
    task_results: Dict[str, TaskResult] = field(default_factory=dict)

    # 彙總策略
    aggregation_strategy: Literal["last_only", "combine_all", "custom"] = "last_only"


# ============================================================================
# Manager 狀態（LangGraph 用）
# ============================================================================


class ManagerState(TypedDict, total=False):
    """
    LangGraph 狀態包

    包含：
    - session_id, query: 必填
    - intent_understanding: 意圖理解結果
    - execution_mode: 執行模式
    - history, short_term_memory: 對話記憶
    - task_graph, current_task_id: 任務規劃
    - task_results: 執行結果
    - hitl_*: Human-in-the-Loop 相關
    - final_response: 最終輸出
    """

    # === 必填 ===
    # last_value_reducer：防止連續對話/多標的查詢失敗 resume 時，
    # 同一 step 多次寫入同一 channel 觸發 INVALID_CONCURRENT_GRAPH_UPDATE。
    # input 的 Command(update={...}) 與 claw_loop node 可能同時寫這些欄位。
    session_id: Annotated[str, last_value_reducer]
    query: Annotated[str, last_value_reducer]

    # === 意圖理解 ===
    intent_understanding: Optional[Dict]  # IntentUnderstanding 的 dict 形式
    execution_mode: Annotated[str, last_value_reducer]  # "vending" or "restaurant"
    system_prompt: Annotated[Optional[str], last_value_reducer]  # 使用者自訂 system prompt
    enabled_tools: List[str]  # 使用者啟用的工具 ID 清單

    # === 記憶 ===
    # history 與其 metadata 由 input Command 與 claw_loop fallback 同時寫入，
    # 全部加 last_value_reducer 避免並發寫入崩潰。
    history: Annotated[str, last_value_reducer]  # 壓縮後的歷史
    # 對話歷史載入 metadata（由 api/routers/analysis.py 與 claw_loop fallback 寫入）。
    # 這 4 個 channel 之前因未宣告於 ManagerState，LangGraph 的 __input__ task
    # 寫入時會記「unknown channel」warning 並丟棄，導致 _main.py:_invoke_response_hooks
    # 讀 state.get("history_truncated", False) 永遠是 False（latent bug）。
    # 宣告於此同時解決 warning 與 latent read。
    history_load_status: Annotated[Optional[str], last_value_reducer]  # "loaded" / "empty" / "error: ..."
    history_truncated: Annotated[Optional[bool], last_value_reducer]
    history_token_count: Annotated[Optional[int], last_value_reducer]
    history_message_count: Annotated[Optional[int], last_value_reducer]
    short_term_memory: Optional[Dict]  # ShortTermMemory 的 dict 形式

    # === 規劃 ===
    task_graph: Optional[Dict]  # TaskGraph 的 dict 形式
    current_task_id: Optional[str]  # 當前執行的任務

    # === 執行 ===
    task_results: Annotated[
        Dict, task_results_reducer
    ]  # {task_id: TaskResult} - 使用自定義 reducer
    hitl_type: Optional[str]
    hitl_question: Optional[str]
    hitl_confirmed: Optional[bool]  # HITL 是否已被確認（用於 resume 後跳過 interrupt）

    # === 輸出 ===
    final_response: Optional[str]
    aggregated_response: Optional[str]
    # Trustworthy AI HITL 場景 B：詐騙判定證據鏈（後端附加，不用 graph interrupt）
    scam_evidence: Optional[Dict]

    # === 重試控制（Reflect-driven Retry）===
    retry_count: int
    retry_hints: Dict[str, str]
    tasks_to_retry: List[str]

    # === 內部狀態 ===
    _processed_query: Optional[str]
    # Phase D（vague-query clarify）的 per-session flag：resume 從 node 開頭重跑，
    # 用 state（非 self）防無限 interrupt。**必須宣告於此**——claw_loop 會寫
    # `_vague_clarified=True` 進 state return，若沒宣告 LangGraph 會以「unknown
    # channel」拒收 update → HITL resume 500（線上 #特斯拉案例根因之一）。
    _vague_clarified: Optional[bool]
    # Phase E（wrong-scope clarify）的 per-session flag：resume 從 node 開頭重跑，
    # 用 state（非 self）防無限 interrupt（同 _vague_clarified）。宣告於此避免
    # LangGraph "unknown channel" warning（照 history_* 欄位先例，line 584-587）。
    _scope_clarified: Optional[bool]
    # Phase F（model-driven clarify，hybrid）的 per-session flag：模型主動呼叫
    # clarify 工具 → node 攔截 interrupt。同 _scope_clarified 用 state 防無限觸發。
    _tool_clarified: Optional[bool]
    # Phase G（skill/memory/journal consent）已處理提案的 identity 清單（2026-08-25）：
    # identity 與 ts 無關（resume 重跑時工具重新提案也能正確去重），並支援一輪
    # 多提案的批次同意（multi_consent）。**必須宣告於此**——未宣告的 channel
    # LangGraph 會拒收 update（同 _vague_clarified 教訓）。
    _handled_consent_ids: List[str]

    # === 控制 ===
    # language 由 input Command 與節點寫入，加 reducer 防並發崩潰。
    language: Annotated[str, last_value_reducer]

    # === Model Mixer（Step 1，c042）===
    # preset_config / preset_id 之前未宣告——LangGraph 對未知 channel 的 input
    # 寫入會「wrote to unknown channel …, ignoring it」靜默丟棄（實測 langgraph
    # 1.2.10），PR #617 的 preset 工具池縮減因此從未真正送達 claw_loop。本 PR
    # 補宣告修復（同 history_* / _vague_clarified 先例）。claw_loop 只讀不寫，
    # input 單一寫入點；新回合的 None 必須清空舊設定，resume 省略更新則保留。
    #
    # ⚠️ 補宣告會「開啟」下游那條路徑，而它當時本身是壞的：base_react_agent
    # 在 preset 分支直接 return 跳過 get_allowed_tools，加上 resolve_preset_config
    # 把所有 seed meta 寫死 required_tier="free"，兩者疊起來讓免費使用者拿得到
    # premium 工具。宣告與那兩處修復必須同進同出，不可只補宣告。
    preset_config: Annotated[Optional[Dict], replace_optional_value]
    preset_id: Annotated[Optional[str], replace_optional_value]
    # 本次 run 實際使用的 LLM（由 analyze 依 per-agent 指定／使用者預設解析後寫入）。
    # RunMetrics 基準線欄位：之後路由準確度／每題成本都靠它對帳。
    llm_model: Annotated[Optional[str], last_value_reducer]
    llm_provider: Annotated[Optional[str], last_value_reducer]


MAX_TASK_RETRIES = 2
