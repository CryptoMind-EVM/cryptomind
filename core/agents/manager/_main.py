"""
Manager Agent - Main Module

Contains the ManagerAgent class definition, graph building, and module-level
utilities (constants, checkpointer, background task runner, etc.).

All mixins are combined here via multiple inheritance to form the complete
ManagerAgent class.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from api.utils import logger
from core.agents.context_budget import (
    CONTEXT_CHAR_BUDGET,
    CompactPrompt,
    format_compact_state,
    history_exceeds_budget,
)

# ============================================================================
# Module-level variables and constants
# ============================================================================
from core.database.experiences import ExperienceStore
from core.tools.symbol_normalizer import SymbolNormalizer
from core.tools.universal_resolver import UniversalSymbolResolver

from ..agent_registry import AgentRegistry
from ..analysis_policy import AnalysisPolicyResolver
from ..models import ManagerState
from ..tool_access_resolver import ToolAccessResolver
from ..tool_registry import ToolRegistry
from ..tracing import TraceCollector
from .claw_loop import ClawLoopMixin
from .llm import LLMInvokeMixin
from .memory import MemoryMixin
from .mixin_base import ManagerAgentMixin

_experience_store = ExperienceStore()

_background_tasks: set = set()


def _run_background(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t):
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            import logging

            logging.getLogger(__name__).error(
                "[Background task] failed: %s", exc, exc_info=exc
            )

    task.add_done_callback(_on_done)
    return task


def _extract_model_name_for_manager(llm: Any) -> str:
    """Extract model name from a (possibly LanguageAwareLLM-wrapped) LLM."""
    inner = getattr(llm, "_llm", llm)
    return (
        getattr(inner, "model_name", None) or getattr(inner, "model", None) or "unknown"
    )


_per_user_checkpointer: Dict[str, MemorySaver] = {}
_CHECKPOINTER_MAX = 512
_checkpointer_lock = threading.Lock()


def _get_checkpointer(user_id: str, session_id: str) -> MemorySaver:
    """Return a per-session checkpointer to prevent cross-user state leakage."""
    key = f"{user_id}:{session_id}"
    with _checkpointer_lock:
        # LRU：每次取用都移到最後（dict 保留插入順序），滿了淘汰最久沒用的。
        # 以前是 FIFO：正在等 HITL 回答的 session 只要夠早建立，就會被新 session
        # 擠掉 → 回答後 resume 對到空的 checkpoint（同意卡的答案遺失）。
        saver = _per_user_checkpointer.pop(key, None) or MemorySaver()
        _per_user_checkpointer[key] = saver
        while len(_per_user_checkpointer) > _CHECKPOINTER_MAX:
            _per_user_checkpointer.pop(next(iter(_per_user_checkpointer)), None)
        return saver


# Memory consolidation trigger threshold
MEMORY_CONSOLIDATION_THRESHOLD = int(os.environ.get("MEMORY_CONSOLIDATION_THRESHOLD", "12"))
MEMORY_IDLE_TIMEOUT = 300  # 閒置 5 分鐘後整合
MANAGER_GRAPH_RECURSION_LIMIT = 60
MAX_GRAPH_TASKS = 8
# ⚠️ Timeout 策略（2026-08-02 改，對齊 ChatGPT/Claude/LangGraph 官方做法）：
#
# 過去用 wall-clock（300s→600s）+ idle timeout（120s），會誤殺合法的深度分析：
# - reasoning model（nemotron）思考期不吐 token → idle 誤殺
# - free tier 排隊（TTFT > 120s）→ idle 誤殺
# - 開放式問題（「美股 vs 比特幣比較」）LLM 要規劃 5+ 分鐘 → wall-clock 切斷
#
# 業界共識（Hermes Issue #4815、LangGraph 官方、Claude Code）：
# 不要用時間（秒數）當主要限制，要用步數（recursion_limit）。
# LangGraph 的 astream 預設 recursion_limit=25，足以擋住無限迴圈，
# 又不會誤殺需要多步的合法分析。
#
# 現在改為：
# - 主要保護：recursion_limit=25（LangGraph 原生，每步 = 一輪 LLM + 工具）
# - AGENT_EXECUTION_TIMEOUT：寬鬆 safety net（預設 300s = 5 分鐘，治理測試
#   規定預設 ≤ 300s——避免卡死的 chat 佔住 worker 太久；env 可覆寫調大）。
# - 移除 AGENT_IDLE_TIMEOUT（不再用 idle 判定，避免誤殺 reasoning/prefill）
#
# SSE 整體上限 ANALYSIS_TIMEOUT_SECONDS=3600 仍是最外層保護。
AGENT_EXECUTION_TIMEOUT = int(os.environ.get("AGENT_EXECUTION_TIMEOUT", "300"))


# ============================================================================
# Context budget helpers (module-level for easy patching in tests)
# ============================================================================


def _read_compact_for_manager(user_id: str, session_id: str) -> Optional[CompactPrompt]:
    """Read compact session state, return as CompactPrompt or None."""
    try:
        from core.database.memory import get_memory_store

        store = get_memory_store(user_id, session_id=session_id)
        state = store.read_compact_state()
        if state is None:
            return None
        return CompactPrompt(
            goal=state.goal,
            progress=state.progress,
            open_questions=state.open_questions,
            next_steps=state.next_steps,
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def _get_history_for_prompt(
    raw_history: str,
    user_id: str,
    session_id: str,
) -> str:
    """Return history string for LLM prompt, respecting CONTEXT_CHAR_BUDGET.

    Priority:
      1. raw_history within budget → return as-is
      2. over budget + compact state available → return formatted compact block
      3. over budget + no compact state → truncate raw history to budget
    """
    import sys

    # Use the facade module's attributes so that test patches on
    # "core.agents.manager.history_exceeds_budget" and
    # "core.agents.manager._read_compact_for_manager" take effect.
    facade = sys.modules.get("core.agents.manager")
    _hEB = facade.history_exceeds_budget if facade else history_exceeds_budget
    _rcfm = facade._read_compact_for_manager if facade else _read_compact_for_manager
    _fcs = facade.format_compact_state if facade else format_compact_state

    if not _hEB(raw_history):
        return raw_history
    compact = _rcfm(user_id, session_id)
    if compact is not None:
        return _fcs(compact)
    # Fallback: truncate to budget (tail — keep most recent)
    return raw_history[-CONTEXT_CHAR_BUDGET:]


# ============================================================================
# TracedGraph — 在 graph.ainvoke 完成後自動記錄 trace summary
# ============================================================================


class _TracedGraph:
    """輕量 proxy，在 ainvoke / invoke 完成後觸發 trace summary logging。

    同時負責把 Langfuse LangChain CallbackHandler 注入到 graph 執行的
    config 中，並用 ``atrace_graph_call`` / ``trace_graph_call`` 包住執行，
    讓 user_id / session_id / metadata 透過 OTel baggage 正確上達 trace
    （v4 SDK 的 baggage 是 context-bound，必須在 with 區塊內執行 graph）。

    LangChain 1.x 的 callback 機制會自動往下傳到內層 LLM 呼叫、tool 呼叫、
    ReAct loop，因此 cryptomind agent 內所有 LLM / tool 都會被統一追蹤。

    Langfuse disabled（未設 keys / TEST_MODE）時：
    - ``_build_langfuse_config`` 回傳原 config 不變
    - ``atrace_graph_call`` / ``trace_graph_call`` 為 no-op
    行為與未引入 Langfuse 完全一致。

    不修改 graph 本身行為，只附加事後 hook + config 注入 + OTel context。
    """

    def __init__(
        self,
        graph: Any,
        on_complete: Callable[[], None],
        user_id: str = "anonymous",
        session_id: str = "default",
    ):
        self._graph = graph
        self._on_complete = on_complete
        self._user_id = user_id
        self._session_id = session_id

    def _attach_langfuse_callback(
        self, config: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """若 Langfuse 啟用，把 CallbackHandler 合併進 config["callbacks"]。

        CallbackHandler 本身不帶 user/session（v4 設計）；user/session 由外層
        ``trace_graph_call`` 寫進 OTel baggage。這裡只負責讓 handler 被
        LangChain 拿去觸發 observations。

        Returns:
            新的 config dict（不變更輸入）；Langfuse disabled 或 handler 建立
            失敗時回傳原 config（含 None 情況）。
        """
        try:
            from utils.langfuse_init import get_langchain_handler
        except ImportError:
            return config

        handler = get_langchain_handler()
        if handler is None:
            return config

        new_config = dict(config) if config else {}
        existing = new_config.get("callbacks")
        if existing is None:
            new_config["callbacks"] = [handler]
        elif isinstance(existing, list):
            new_config["callbacks"] = [*existing, handler]
        else:
            # LangChain 也接受 tuple / manager；保守地轉成 list
            new_config["callbacks"] = [existing, handler]
        return new_config

    async def ainvoke(
        self, input: Any, config: Optional[Any] = None, **kwargs: Any
    ) -> Any:
        """代理 graph.ainvoke，完成後記錄 trace summary。"""
        try:
            from utils.langfuse_init import atrace_graph_call
        except ImportError:
            atrace_ctx = None
        else:
            atrace_ctx = atrace_graph_call(
                user_id=self._user_id,
                session_id=self._session_id,
            )

        async def _run():
            cfg = self._attach_langfuse_callback(config)
            return await self._graph.ainvoke(input, cfg, **kwargs)

        try:
            if atrace_ctx is None:
                result = await _run()
            else:
                async with atrace_ctx:
                    result = await _run()
            return result
        finally:
            try:
                self._on_complete()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

    def invoke(self, input: Any, config: Optional[Any] = None, **kwargs: Any) -> Any:
        """代理 graph.invoke，完成後記錄 trace summary。"""
        try:
            from utils.langfuse_init import trace_graph_call
        except ImportError:
            trace_ctx = None
        else:
            trace_ctx = trace_graph_call(
                user_id=self._user_id,
                session_id=self._session_id,
            )

        def _run():
            cfg = self._attach_langfuse_callback(config)
            return self._graph.invoke(input, cfg, **kwargs)

        try:
            if trace_ctx is None:
                result = _run()
            else:
                with trace_ctx:
                    result = _run()
            return result
        finally:
            try:
                self._on_complete()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

    def __getattr__(self, name: str) -> Any:
        """透明代理其他屬性（如 get_graph, stream 等）。"""
        return getattr(self._graph, name)


# ============================================================================
# ManagerAgent
# ============================================================================


class ManagerAgent(
    ClawLoopMixin,
    LLMInvokeMixin,
    MemoryMixin,
    ManagerAgentMixin,
):
    """
    ManagerAgent — CLAW 單迴圈架構（Hermes / OpenClaw 式）。

    一個 LangGraph 節點 `claw_loop`：把用戶的查詢直接交給 cryptomind ReAct
    loop，LLM 自己讀 tool descriptions 決定該做什麼。寫最終答案的模型與看到
    工具原始輸出的模型是同一個 context，結構性消除舊 5 節點管線（intent →
    execute → aggregate → reflect → synthesize）裡「合成層看不到工具輸出而
    腦補」的幻覺來源。

    特點：
    - 單一 ReAct loop（一題一條 LLM 迴圈）
    - 零硬編碼路由（LLM 自行從 tool descriptions 選工具）
    - 長期記憶整合（持久化）
    """

    def __init__(
        self,
        llm_client,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        web_mode: bool = False,
        user_tier: str = "free",
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        display_name: Optional[str] = None,
        wallet_address: Optional[str] = None,
    ):
        self.llm = llm_client
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        self.web_mode = web_mode
        self.user_tier = user_tier

        # 用戶和會話標識
        self.user_id = user_id or "anonymous"
        self.session_id = session_id or "default"
        # Trustworthy AI — Principal：個人化身份（cache-hit 時由 bootstrap re-apply）
        self.display_name = display_name
        self.wallet_address = wallet_address
        self.tool_access_resolver = ToolAccessResolver(
            user_tier=self.user_tier, user_id=self.user_id
        )
        self.analysis_policy_resolver = AnalysisPolicyResolver()

        # 短期記憶（每個 session 獨立）
        self._memory_cache: Dict[str, Any] = {}

        # 長期記憶存儲（延遲初始化）
        self._memory_store = None

        # 進度回調
        self.progress_callback: Optional[Callable] = None

        # 記憶整合控制（nanobot 風格）
        self._consolidating = False  # 是否正在整合中
        self._consolidation_lock = asyncio.Lock()  # 整合鎖
        self._last_consolidated_index = 0  # 已整合的消息索引
        self._consolidation_task: Optional[asyncio.Task] = None  # 背景整合任務

        self._message_count = 0  # 當前會話消息計數
        self._last_activity_time = time.time()  # 最後活動時間（用於閒置整合）

        # Token 追蹤與模型路由
        from ..model_router import ModelRouter
        from ..token_tracker import TokenTracker

        self._model_router = ModelRouter()
        self._token_tracker = TokenTracker()

        # System prompt for manager-level LLM calls (intent understanding,
        # synthesis, streaming).  Empty by default — the detailed prompt
        # templates from PromptRegistry are passed as the user message.
        self._system_prompt: str = ""

        # 結構化追蹤
        self._trace_collector = TraceCollector(
            session_id=self.session_id,
        )

        # 建立 LangGraph
        raw_graph = self._build_graph()
        self.graph = _TracedGraph(
            raw_graph,
            self._log_trace_summary,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        self._symbol_resolver = UniversalSymbolResolver()
        self._symbol_normalizer = SymbolNormalizer()

    def _build_graph(self) -> StateGraph:
        """建立 LangGraph 狀態圖 — 單節點 CLAW 直通模式。

        Hermes/OpenClaw 式的單一 ReAct loop：寫最終答案的模型與看到工具輸出
        的模型同一個 context。沒有 intent 分解 / aggregate / reflect / synthesize
        等額外 LLM call。
        """
        builder = StateGraph(ManagerState)
        builder.add_node("claw_loop", self._wrap_with_trace(self._claw_loop_node))
        builder.set_entry_point("claw_loop")
        builder.add_edge("claw_loop", END)
        return builder.compile(
            checkpointer=_get_checkpointer(self.user_id, self.session_id)
        )

    def _wrap_with_trace(self, node_fn):
        """將 node function 包上 tracing wrapper。

        trace 記錄完全在 try/except 中，失敗不影響主流程。
        wrapper 透過 TraceCollector.start_trace / finish_trace 記錄
        節點的開始時間、結束時間和輸出摘要。
        """

        async def traced_node(state: ManagerState) -> Dict:
            node_name = node_fn.__name__
            trace = None
            # _token_tracker 跨請求累計；記下 node 開始前的基準，結束時算增量，
            # 才能拿到「本次 node」的 token 用量（而非歷史總和）。
            token_baseline = 0
            try:
                # query 走 LangGraph state 通道，TraceCollector 物件層讀不到；
                # 在節點邊界銜接：把 state 的 query 寫回 collector，讓 trace
                # summary 能顯示真實 query（否則永遠是空字串）。
                state_query = state.get("query", "")
                if state_query and not self._trace_collector.query:
                    self._trace_collector.query = state_query
                trace = self._trace_collector.start_trace(
                    node_name,
                    input_summary=state_query,
                )
                token_baseline = self._token_tracker.total_requests()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

            try:
                result = await node_fn(state)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                # trace 記錄錯誤，但仍然 re-raise 讓 LangGraph 處理
                try:
                    if trace:
                        self._trace_collector.finish_trace(
                            trace,
                            error=str(exc)[:200],
                            token_usage=self._token_tracker.usage_since(
                                token_baseline
                            ),
                        )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
                raise

            # 正常完成，記錄 trace
            try:
                if trace:
                    output_summary = (
                        result.get("final_response")
                        or result.get("aggregated_response")
                        or None
                    )
                    self._trace_collector.finish_trace(
                        trace,
                        output_summary=output_summary,
                        token_usage=self._token_tracker.usage_since(
                            token_baseline
                        ),
                    )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

            return result

        # 保留原始函數名稱，方便 debug
        traced_node.__name__ = node_fn.__name__
        traced_node.__wrapped__ = node_fn  # type: ignore[attr-defined]
        return traced_node

    def _log_trace_summary(self) -> None:
        """記錄完整 trace summary 到 logger。在 graph 執行完成後呼叫。"""
        try:
            log_text = self._trace_collector.format_trace_log()
            logger.info("[Trace Summary]\n%s", log_text)
            # 重置 collector 供下次請求使用
            self._trace_collector.reset()
            self._trace_collector.session_id = self.session_id
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass

    def _emit_progress(self, stage: str, message: str, **extra):
        """發送進度事件"""
        if self.progress_callback:
            payload = {
                "stage": stage,
                "message": message,
            }
            payload.update(extra)
            self.progress_callback(payload)

    def _invoke_response_hooks(
        self,
        state: dict,
        final_response: str,
        execution_time_ms: float = 0.0,
    ) -> None:
        """Fire post-response hooks (fire-and-forget).

        Args:
            state: The graph state dictionary
            final_response: The final response string
            execution_time_ms: Execution time in milliseconds
        """
        try:
            # Collect tools used from task results
            tools_used = []
            task_results = state.get("task_results", {})
            if isinstance(task_results, dict):
                for result in task_results.values():
                    if isinstance(result, dict):
                        used = result.get("data", {}).get("used_tools", [])
                        if isinstance(used, list):
                            tools_used.extend(used)

            # Import here to avoid circular imports
            from core.agents.hooks import (
                ResponseContext,
                invoke_response_hooks_sync,
            )

            ctx = ResponseContext(
                user_id=self.user_id or "anonymous",
                session_id=self.session_id or "",
                query=state.get("query", ""),
                response=final_response,
                agent_name="manager",
                tools_used=list(set(tools_used)),
                execution_time_ms=execution_time_ms,
                language=self.language,
                metadata={
                    "history_truncated": state.get("history_truncated", False),
                    "execution_mode": state.get("execution_mode", "vending"),
                },
            )
            invoke_response_hooks_sync(ctx)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            # Never let hooks block the main flow
            pass
