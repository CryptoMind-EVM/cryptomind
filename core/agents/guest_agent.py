"""訪客 agent — 跟免費會員同一個 CryptoMindAgent，只開唯讀、不綁使用者的市場工具。

設計：docs/plans/2026-09-27-launch-readiness-design.md §4 PR-4；
實作取捨：docs/plans/2026-09-27-pr4-guest-agent-impl.md。

為什麼不跑 ManagerAgent：manager 綁了 session checkpointer、manager 快取、記憶讀寫、
回覆快取、consent gate、post-response hooks（wiki_capture 寫知識庫）——全都假設「有使用者」。
訪客只借工具定義（build_tool_registry）與 agent 本體，跑 execute_streaming（登入免費會員在
claw_loop 裡用的同一條 ReAct 路徑），不經 claw_loop，所以零 DB 寫入。

安全邊界：
- agent 手上的 registry **只有白名單工具**（不是「全部工具再濾」）；
- user_id=None：execute_streaming 會把 current_user_id contextvar 設成 None；
- 白名單工具的原始碼不讀 get_current_user_id（tests/test_guest_agent.py 守著）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from functools import lru_cache
from typing import Any, Optional

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage

from .agents import CryptoMindAgent
from .agents.cryptomind_agent import _current_time_line
from .models import SubTask
from .prompt_registry import PromptRegistry
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# 訪客可用工具：明確清單，新工具預設**不**給訪客（要給就加在這裡，測試會驗）。
# 條件：tools_catalog tier_required=free、非 high-risk、不讀寫使用者資料、不需要使用者自己的金鑰。
GUEST_TOOL_ALLOWLIST: tuple[str, ...] = (
    # 加密貨幣行情／指標
    "get_crypto_price",
    "technical_analysis",
    "get_fear_and_greed_index",
    "get_trending_tokens",
    "get_crypto_market_cap",
    "get_gas_fees",
    "get_exchange_flow",
    "get_staking_yield",
    "get_btc_network_status",
    "get_eth_price_etherscan",
    # DEX／合約（公開鏈上資料）
    "get_dex_pair_info",
    "get_trending_dex_pairs",
    "get_contract_info",
    # 詐騙查詢（唯讀）
    "check_token_security",
    "check_address_safety",
    # 新聞／搜尋
    "google_news",
    "aggregate_news",
    "web_search",
    # 美股／台股公開行情
    "us_stock_price",
    "us_technical_analysis",
    "us_news",
    "sec_filings",
    "tw_stock_price",
    "tw_technical_analysis",
    "tw_news",
    "tw_major_news",
    # 總經／通用
    "get_economic_calendar",
    "get_current_time_taipei",
    "resolve_symbol",
)

# 延遲控制：本地 9B 模型每多一輪工具就多一次完整推論。第 4 次起工具被擋（模型改用手上資料作答）；
# 模型呼叫硬上限＝工具輪數＋收尾＋一次緩衝，超過直接當失敗（呼叫端退回單次回答）。
GUEST_MAX_TOOL_CALLS = 3
GUEST_MAX_MODEL_CALLS = GUEST_MAX_TOOL_CALLS + 2

# 只對登入者成立的 prompt 段落：教模型呼叫 remember／list_my_skills_memory、宣稱「平台有你的記憶」、
# 用 clarify 工具（HITL）暫停對話——訪客都沒有。釐清改由訪客規則「用一句話反問」處理。
GUEST_OMITTED_PROMPT_SECTIONS: tuple[str, ...] = (
    "proactive_memory",
    "memory_identity",
    "memory_citation",
    "self_manage_skills",
    "scope_clarification",
)


class GuestAgentFailed(RuntimeError):
    """agent 沒產出可用回答（錯誤結果、空回覆、呼叫上限）——呼叫端改走單次回答。"""


def select_guest_tools(source: ToolRegistry) -> ToolRegistry:
    """從完整 registry 挑出白名單工具，組成只含這些工具的新 registry。"""
    from core.database.tools import get_tool_risk_level

    picked = ToolRegistry()
    for name in GUEST_TOOL_ALLOWLIST:
        meta = source._tools.get(name)
        if meta is None:
            logger.warning("[GuestAgent] allowlisted tool %s is not registered", name)
            continue
        # risk_level 在 bootstrap 尾端才注入；這裡直接從單一真相來源補上（不改原物件）
        picked.register(dataclasses.replace(meta, risk_level=get_tool_risk_level(name)))
    return picked


@lru_cache(maxsize=1)
def build_guest_tool_registry() -> ToolRegistry:
    """訪客工具 registry（每個進程建一次；handler 是模組層 LangChain tool，可共用）。"""
    from .bootstrap import build_tool_registry

    return select_guest_tools(build_tool_registry(None))


class GuestCryptoMindAgent(CryptoMindAgent):
    """沒有使用者的 CryptoMindAgent：工具只剩白名單、prompt 拿掉記憶段落、呼叫次數有上限。"""

    def __init__(
        self, llm_client: Any, tool_registry: ToolRegistry, guest_rules: str = ""
    ):
        super().__init__(llm_client, tool_registry, user_tier="free", user_id=None)
        self.guest_rules = guest_rules

    def _get_system_prompt(self, language: str) -> str:
        time_line = _current_time_line(language)
        prompt = super()._get_system_prompt(language)
        # 時間錨點每分鐘變；放最前面會讓本地 llama-server 的 prompt 快取每分鐘整段失效。
        # 訪客 prompt 把它移到最後，前面的靜態部分跨請求都能重用（剛好跨分鐘就維持原位）。
        moved = prompt.startswith(time_line)
        if moved:
            prompt = prompt[len(time_line) :]
        for section in GUEST_OMITTED_PROMPT_SECTIONS:
            text = PromptRegistry.get("shared", section, language)
            if text:
                prompt = prompt.replace(text, "")
        prompt = re.sub(r"\n{3,}", "\n\n", prompt).strip()
        if self.guest_rules:
            prompt = f"{prompt}\n\n{self.guest_rules}"
        if moved:
            prompt = f"{prompt}\n\n{time_line}"
        return prompt

    def _inject_skill_instructions(self, system_prompt: str, task: SubTask) -> str:
        # 訪客沒有 load_skill（它會讀使用者的 skill 偏好）：不放「請呼叫 load_skill」的目錄，
        # 改由 harness 依關鍵字帶最相關的一個 skill 正文（最多一個，控制 prompt 長度）。
        context = task.context if isinstance(task.context, dict) else {}
        query = context.get("original_query") or task.description or ""
        if not query:
            return system_prompt
        try:
            from .skill_loader import get_skill_loader

            loader = get_skill_loader()
            skills = loader.match_skills(
                query=query, agent_name=self.name, max_matches=1
            )
            instructions = loader.get_instructions(skills) if skills else ""
        except (OSError, ValueError, KeyError) as exc:
            logger.debug("[GuestAgent] skill injection skipped: %s", type(exc).__name__)
            return system_prompt
        return system_prompt + (instructions or "")

    def _extra_agent_middleware(self) -> list:
        return [
            ToolCallLimitMiddleware(
                run_limit=GUEST_MAX_TOOL_CALLS, exit_behavior="continue"
            ),
            ModelCallLimitMiddleware(
                run_limit=GUEST_MAX_MODEL_CALLS, exit_behavior="error"
            ),
        ]

    @staticmethod
    def _parse_history_to_messages(history: Any) -> list:
        """訪客歷史是 API 驗證過的 [{role, content}]，直接轉成角色訊息。

        不走基類的「用戶:／助手:」文字解析——內容裡剛好有那種前綴也不會被當成換角色。
        """
        messages: list = []
        for item in history if isinstance(history, list) else []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if item.get("role") == "user":
                messages.append(HumanMessage(content=content))
            elif item.get("role") == "assistant":
                messages.append(AIMessage(content=content))
        return messages


def build_guest_agent(llm_client: Any, guest_rules: str = "") -> GuestCryptoMindAgent:
    from .skill_loader import get_skill_loader

    # 第一次會讀 prompts／skills 檔（之後都是快取）；run_guest_agent 把這步丟 thread
    PromptRegistry.load()
    get_skill_loader()
    return GuestCryptoMindAgent(llm_client, build_guest_tool_registry(), guest_rules)


async def run_guest_agent(
    llm_client: Any,
    message: str,
    history: Optional[list[dict]],
    language: str,
    guest_rules: str = "",
) -> str:
    """跑一次訪客 agent，回傳清理過的回答；沒有可用回答就丟 GuestAgentFailed。

    不串流（呼叫端回 JSON）、不寫任何東西；逾時由呼叫端的 asyncio.wait_for 取消。
    """
    from .manager.claw_loop import _clean_claw_response
    from .tool_guard import reset_turn_tool_guard

    agent = await asyncio.to_thread(build_guest_agent, llm_client, guest_rules)
    task = SubTask(
        step=1,
        description=message,
        agent=agent.name,
        context={
            "language": language,
            "original_query": message,
            "history": list(history or []),
        },
    )
    reset_turn_tool_guard()  # 同 claw_loop：每題重設 web_search 去重／輪數
    result = await agent.execute_streaming(task)
    if not result.success:
        raise GuestAgentFailed("agent returned an error result")
    reply = _clean_claw_response(str(result.message or ""))
    if not reply:
        raise GuestAgentFailed("agent returned an empty reply")
    return reply
