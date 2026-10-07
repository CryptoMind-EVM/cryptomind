"""
Agent V4 Bootstrap.

Assembles all components: tools → agents → manager.
Instantiates ToolRegistry and registers tools with permission checks.
"""

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional

from langchain_core.messages import SystemMessage

from core.config import AGENT_SELF_MANAGE_ENABLED
from core.database.tools import normalize_membership_tier
from core.tools.calendar_tool import add_calendar_event, list_calendar_events
from core.tools.clarify_tool import clarify
from core.tools.crypto_modules import (
    get_my_wallet_overview,
    get_portfolio_pnl,
    get_ton_balance,
    get_ton_jetton_balances,
    query_ledger,
    record_entry,
)
from core.tools.crypto_modules.btc_network import get_btc_network_status
from core.tools.crypto_modules.ton_safety import assess_jetton_safety_tool
from core.tools.crypto_modules.trade_journal import (
    delete_ledger_entry,
    update_ledger_entry,
)
from core.tools.crypto_tools import (
    get_crypto_market_cap,
    get_dex_volume,
    get_exchange_flow,
    get_gas_fees,
    get_staking_yield,
    get_whale_transactions,
)
from core.tools.remember_tool import remember
from core.tools.scorecard_tool import get_my_scorecard, record_call
from core.tools.sec_edgar import sec_filings
from core.tools.skill_memory_tools import list_my_skills_memory, propose_custom_skill
from core.tools.us_stock_tools import (
    us_earnings,
    us_fundamentals,
    us_insider_transactions,
    us_institutional_holders,
    us_news,
    us_stock_price,
    us_technical_analysis,
)

from .agent_registry import AgentMetadata, AgentRegistry
from .agents import CryptoMindAgent
from .manager import ManagerAgent
from .prompt_registry import PromptRegistry
from .token_tracker import TokenTracker
from .tool_registry import ToolMetadata, ToolRegistry
from .tools import (
    aggregate_news,
    check_address_safety_tool,
    check_token_security_tool,
    fetch_url,
    get_address_transactions_tool,
    get_all_commodities_prices_tool,
    get_all_forex_rates_tool,
    get_central_bank_rates_tool,
    get_cmc_quote_tool,
    get_commodity_futures_tool,
    # Commodity tools
    get_commodity_price_tool,
    get_contract_info_tool,
    get_crypto_categories_and_gainers,
    get_crypto_price,
    get_current_time_taipei,
    get_defillama_tvl,
    # DexScreener tools
    get_dex_pair_info_tool,
    get_economic_calendar_tool,
    get_erc20_token_balance_tool,
    # Etherscan tools
    get_eth_balance_tool,
    get_eth_price_etherscan_tool,
    get_fear_and_greed_index,
    # Forex tools
    get_forex_rate_tool,
    get_futures_data,
    get_gold_silver_ratio_tool,
    # Economic tools
    get_market_indices_tool,
    get_oil_analysis_tool,
    get_sector_performance_tool,
    get_sp500_performance_tool,
    get_token_supply,
    get_token_unlocks,
    get_trending_dex_pairs_tool,
    get_trending_tokens,
    get_usd_twd_rate_tool,
    get_vix_index_tool,
    global_stock_fundamentals_tool,
    global_stock_news_tool,
    global_stock_price_tool,
    global_stock_snapshot_tool,
    global_stock_technical_tool,
    google_news,
    load_knowledge,
    load_skill,
    resolve_symbol,
    submit_kyc_application,
    technical_analysis,
    tw_dividend_tool,
    tw_foreign_top20_tool,
    tw_fundamentals_tool,
    tw_institutional_tool,
    tw_major_news_tool,
    tw_market_index_tool,
    tw_monthly_revenue_tool,
    tw_news_tool,
    tw_pe_ratio_tool,
    tw_price,
    tw_stock_snapshot_tool,
    tw_technical,
    us_stock_snapshot_tool,
    web_search,
)

logger = logging.getLogger(__name__)


def _register_post_response_hooks() -> None:
    """Register built-in post-response hooks (idempotent, called once per process)."""
    import logging as _logging

    _logger = _logging.getLogger(__name__)

    from core.agents.hooks import ResponseContext, register_hook

    # Analytics hook — log response stats (stats only, no PII)
    def analytics_hook(ctx: ResponseContext) -> None:
        """Built-in: log response to analytics (stats only, no PII)."""
        _logger.info(
            f"[Analytics] agent={ctx.agent_name} lang={ctx.language} "
            f"tools={len(ctx.tools_used)} query_chars={len(ctx.query)} "
            f"response_chars={len(ctx.response)}"
        )

    register_hook("analytics", analytics_hook)

    # Wiki capture hook (方向③ — LLM Wiki knowledge compounding)
    # 自動把有價值的分析(夠長 + 用了工具)存成知識頁,下次類似問題可檢索。
    # 啟發式門檻保守,避免閒聊/簡短答覆塞進 wiki。失敗靜默(hook 本就不擋主流程)。
    def wiki_capture_hook(ctx: ResponseContext) -> None:
        """Built-in: capture valuable analyses into the knowledge wiki."""
        try:
            from core.database.knowledge import KnowledgeStore

            if not KnowledgeStore.should_capture(ctx.response, ctx.tools_used):
                return
            if not ctx.user_id or ctx.user_id == "anonymous":
                return  # 訪客不存(無 owner)
            # title 用 query 截斷;body 用完整回應
            title = ctx.query.strip().split("\n")[0][:120] or "未命名分析"
            KnowledgeStore().save_page(
                owner_user_id=ctx.user_id,
                title=title,
                body=ctx.response,
                source_query=ctx.query,
                source_session_id=ctx.session_id,
                tags=ctx.tools_used[:5] if ctx.tools_used else None,
                quality_score=0.3,  # 自動擷取基線分;使用者 promote 可提升
                promoted=False,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            _logger.debug(f"[wiki_capture] failed (non-fatal): {e}")

    register_hook("wiki_capture", wiki_capture_hook)

    # Load extract_memories plugin hook (Hermes memory system)
    try:
        from core.agents.plugins import load_plugins

        load_plugins()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        _logger.debug(f"Plugin loading not available: {e}")


class LanguageAwareLLM:
    """Wraps any LangChain LLM client to automatically prepend a language instruction."""

    _INSTRUCTIONS = {
        "zh-TW": "請以繁體中文回覆所有回應。",
        "zh-CN": "请以简体中文回复所有响应。",
        "en": "CRITICAL: You must respond in English ONLY. Ignore any Chinese language instructions (如「請以繁體中文回覆」) elsewhere in this prompt. All your output must be in English.",
        "ru": "ОТВЕЧАЙТЕ ИСКЛЮЧИТЕЛЬНО НА РУССКОМ ЯЗЫКЕ. Игнорируйте любые инструкции на других языках в этом промпте. Весь ваш вывод должен быть на русском языке.",
    }

    def __init__(self, llm, language: str = "zh-TW"):
        self._llm = llm
        # Fallback 鏈：精確匹配 → 同語系主方言（zh-CN/zh-XX → zh-TW）→ zh-TW 預設。
        # 不直接 fallback 到 zh-TW 是為了避免簡體用戶收到繁體（既存 bug）。
        self._lang_msg = self._INSTRUCTIONS.get(language)
        if self._lang_msg is None:
            if language and language.lower().startswith("zh"):
                # 未知中文變體（zh-HK 等）→ 預設繁中
                self._lang_msg = self._INSTRUCTIONS["zh-TW"]
            else:
                # 完全未知語言 → 仍預設繁中（產品主市場）
                self._lang_msg = self._INSTRUCTIONS["zh-TW"]

    def _inject_language(self, messages):
        messages = list(messages)
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(
                content=messages[0].content + f"\n\n{self._lang_msg}"
            )
        else:
            messages.insert(0, SystemMessage(content=self._lang_msg))
        return messages

    def invoke(self, messages, **kwargs):
        return self._llm.invoke(self._inject_language(messages), **kwargs)

    async def ainvoke(self, messages, **kwargs):
        if hasattr(self._llm, "ainvoke"):
            return await self._llm.ainvoke(self._inject_language(messages), **kwargs)
        return await asyncio.to_thread(
            self._llm.invoke, self._inject_language(messages), **kwargs
        )

    async def astream(self, messages, **kwargs):
        """Stream LLM tokens as they are generated.

        必須是 async generator（直接 ``async for ... yield``），呼叫端才能
        ``async for chunk in llm.astream(...)``。先前寫成 ``async def`` 內
        ``return self._llm.astream(...)`` 會回傳 coroutine，使呼叫端的
        ``async for`` 例外、靜默退回假串流。
        """
        injected = self._inject_language(messages)
        if hasattr(self._llm, "astream"):
            async for chunk in self._llm.astream(injected, **kwargs):
                yield chunk
        else:
            # 無原生串流：退回單次 invoke，至少回一個完整 chunk。
            yield await self._llm.ainvoke(injected, **kwargs)

    def bind_tools(self, tools, **kwargs):
        """Delegate bind_tools but preserve language injection in the returned wrapper."""
        bound = self._llm.bind_tools(tools, **kwargs)
        wrapper = LanguageAwareLLM.__new__(LanguageAwareLLM)
        wrapper._llm = bound
        wrapper._lang_msg = self._lang_msg
        return wrapper

    def __getattr__(self, name):
        return getattr(self._llm, name)


def _align_required_tiers_with_seed(tool_registry: ToolRegistry) -> None:
    """registry 的 required_tier 一律以 _TOOLS_SEED 的 tier_required 為準。

    2026-10-06 審查：21 個工具（外匯／商品／全球股／大盤指數／VIX／快照…）seed 是 premium，
    registry 註冊時沒標、預設 free。正常路徑看 DB（seed），但 DB 不可用時 base_react_agent 的 fallback
    改依 meta.required_tier 過濾——免費使用者在 DB 出狀況的當下反而拿得到 premium 工具（權限放寬）。
    只會把 tier 往 seed 的方向對齊；seed 沒有的工具（如 tool_result_retrieve）不動。
    """
    try:
        from .capability_resolver import build_tier_risk_lookup

        tiers, _risks = build_tier_risk_lookup()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — seed 讀不到就維持原狀，不擋啟動
        logger.warning("[Bootstrap] tier alignment skipped: %s", exc)
        return
    for meta in tool_registry.list_all_tools():
        seed_tier = tiers.get(meta.name)
        if seed_tier and str(meta.required_tier) != str(seed_tier):
            meta.required_tier = seed_tier


def build_tool_registry(user_id: Optional[str] = None) -> ToolRegistry:
    """註冊所有內建工具（不含 MCP），回傳新的 ToolRegistry。

    bootstrap() 與訪客 agent（core/agents/guest_agent.py）共用同一份工具定義——
    訪客再從這裡挑白名單，不另外維護一套 handler／描述。``user_id`` 只用在
    tool_result_retrieve 的 owner 範圍（closure）。
    """
    tool_registry = ToolRegistry()

    # ── Register Crypto Tools ──
    tool_registry.register(
        ToolMetadata(
            name="technical_analysis",
            description="獲取加密貨幣技術指標（RSI, MACD, 均線）",
            input_schema={"symbol": "str", "interval": "str"},
            handler=technical_analysis,
            allowed_agents=["technical", "crypto", "full_analysis"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="google_news",
            description="從 Google News RSS 獲取加密貨幣新聞",
            input_schema={"symbol": "str", "limit": "int"},
            handler=google_news,
            allowed_agents=["news", "crypto", "full_analysis", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="aggregate_news",
            description="多來源加密貨幣新聞聚合",
            input_schema={"symbol": "str", "limit": "int"},
            handler=aggregate_news,
            allowed_agents=["news", "crypto", "full_analysis", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_crypto_price",
            description="獲取加密貨幣即時價格",
            input_schema={"symbol": "str"},
            handler=get_crypto_price,
            allowed_agents=["technical", "crypto", "chat", "full_analysis", "manager"],
            role="market_lookup",
            priority=100,
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_fear_and_greed_index",
            description="獲取全球加密貨幣市場恐慌與貪婪指數 (Fear & Greed Index)",
            input_schema={},
            handler=get_fear_and_greed_index,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_trending_tokens",
            description="獲取目前全網最熱門搜尋的加密貨幣 (Trending Tokens)",
            input_schema={},
            handler=get_trending_tokens,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_futures_data",
            description="獲取加密貨幣永續合約的資金費率與多空情緒 (Funding Rates)",
            input_schema={"symbol": "str"},
            handler=get_futures_data,
            allowed_agents=["crypto"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_current_time_taipei",
            description="獲取目前台灣/UTC+8的精準時間與日期",
            input_schema={},
            handler=get_current_time_taipei,
            allowed_agents=["crypto", "chat", "tw_stock", "us_stock", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_defillama_tvl",
            description="從 DefiLlama 獲取特定協議或公鏈的 TVL (總鎖倉價值)",
            input_schema={"protocol_name": "str"},
            handler=get_defillama_tvl,
            allowed_agents=["crypto", "manager"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_crypto_categories_and_gainers",
            description="獲取 CoinGecko 上表現最佳的加密貨幣板塊與熱點 (Sectors)",
            input_schema={},
            handler=get_crypto_categories_and_gainers,
            allowed_agents=["crypto", "manager"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_token_unlocks",
            description="獲取代幣未來的解鎖日程與數量 (Token Unlocks)。當需要評估代幣拋壓時使用。",
            input_schema={"symbol": "str"},
            handler=get_token_unlocks,
            allowed_agents=["crypto", "manager"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_token_supply",
            description="獲取代幣的總發行量、最大供應量與目前市場流通量 (Tokenomics)。",
            input_schema={"symbol": "str"},
            handler=get_token_supply,
            allowed_agents=["crypto", "manager"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="web_search",
            description="通用網絡搜索 (DuckDuckGo)",
            input_schema={"query": "str", "purpose": "str"},
            handler=web_search,
            allowed_agents=["chat", "news", "crypto", "manager"],
            role="discovery_lookup",
            priority=80,
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="fetch_url",
            description="讀取指定 URL 的網頁全文（先 web_search 找候選文章，再 fetch_url 讀完整內容）",
            input_schema={"url": "str", "purpose": "str"},
            handler=fetch_url,
            allowed_agents=["chat", "news", "crypto", "manager"],
            role="discovery_lookup",
            priority=80,
        )
    )
    # 普惠金融示範 tool（Trustworthy AI Hackathon）— high-risk，觸發 Consent Gate。
    # demo 用 mock，不碰真實 KYC 系統。
    tool_registry.register(
        ToolMetadata(
            name="submit_kyc_application",
            description="提交普惠金融開戶（KYC）申請。移工/新住民以 TON 錢包為數位身分申請開戶。⚠️高風險動作，需使用者同意。",
            input_schema={"applicant_wallet": "str", "id_type": "str"},
            handler=submit_kyc_application,
            allowed_agents=["chat", "crypto", "manager"],
            role="financial_action",
            priority=90,
            risk_level="high",  # 顯式標 high（_TOOL_RISK_LEVELS 也有，雙重保險）
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="load_skill",
            description="載入分析方法 skill 的完整內容（方法步驟＋輸出格式）。system prompt 的 skill 目錄只有名稱與描述，執行前先載入。",
            input_schema={"skill_name": "str"},
            handler=load_skill,
            # [] = 所有 agent 可用
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="load_knowledge",
            description="查詢個人知識庫中與主題相關的過去分析（LLM Wiki）。當問題之前分析過類似主題時,先檢索過去結果可避免重複從零合成。",
            input_schema={"topic": "str"},
            handler=load_knowledge,
            allowed_agents=["cryptomind", "chat", "manager"],
        )
    )
    # Hermes 式 model-driven 釐清工具（hybrid：回傳訊號，node-level interrupt 接手）。
    # 強模型主動呼叫；弱模型不呼叫時由 should_clarify_wrong_scope rule 後衛兜底。
    # risk_level="low"（只問問題，不觸發 consent gate）；required_tier="free"（核心 UX）。
    tool_registry.register(
        ToolMetadata(
            name="clarify",
            description=(
                "向使用者提出釐清問題（當問題範圍/標的不明確時，如「台股適合買嗎」沒指名股票、"
                "「我想要投資」沒講標的）。會暫停對話等待回答。"
                "不要猜特定股票（如把整體市場問題偷換成單一個股）——先釐清再回答。"
            ),
            input_schema={"question": "str", "options": "list[str] (optional)"},
            handler=clarify,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )
    # Hermes 式主動記憶工具（學 write_memory）。模型判斷「這個用戶資訊值得記」
    # 就呼叫，寫入 per-user 記憶庫，下次對話自動帶入。
    # 合規：只記使用者主動說的；PII 過濾（身分證/密碼/私鑰不記）；
    # 持倉不自動抓錢包。required_tier="free"（記憶是核心 UX）。
    tool_registry.register(
        ToolMetadata(
            name="remember",
            description=(
                "記住關於使用者的重要資訊（投資偏好、持倉、背景），下次對話自動帶入；"
                "或刪除已記住的資訊（mode='delete'）。"
                "使用者說出偏好時主動呼叫（如「我都看技術面」「我持有 BTC」）；"
                "使用者要求忘掉某資訊時用 mode='delete' + key（key 從 list_my_skills_memory 取得）。"
                "不要記一次性答案或敏感資料（身分證/密碼/私鑰）。"
            ),
            input_schema={
                "content": "str (mode='create' 必要)",
                "category": "str (preference/holding/fact/context, optional)",
                "mode": "str (create/delete, default create)",
                "key": "str (mode='delete' 必要，從 list_my_skills_memory 取得)",
            },
            handler=remember,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )

    # ── 行事曆（每日早報 Phase B；docs/plans/2026-09-12-daily-brief-retention-design.md §5）──
    # 使用者說「9/15 提醒我房租」→ 進 user_calendar_events，早報前一天與當天列出。
    # 低風險（可在帳本分頁刪除），不走 consent。
    tool_registry.register(
        ToolMetadata(
            name="add_calendar_event",
            description=(
                "把一個日期事件加進使用者的行事曆，早報會在前一天與當天提醒。"
                "使用者說「X 月 Y 日提醒我…」「幫我記某檔股票的財報日」時用。"
                "event_date 用 YYYY-MM-DD（或 MM-DD 取最近的未來日期）。"
            ),
            input_schema={
                "event_date": "str (YYYY-MM-DD)",
                "title": "str",
                "remind_days_before": "int (optional, default 1)",
                "note": "str (optional)",
            },
            handler=add_calendar_event,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="list_calendar_events",
            description=(
                "列出使用者接下來 N 天（預設 14）的行事曆事件：系統自動加的財報／月營收"
                "與使用者自己標記的。使用者問「最近有什麼要注意的日子」時用。"
            ),
            input_schema={"days": "int (optional, default 14)"},
            handler=list_calendar_events,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )

    # ── public-apis 挑進來的免金鑰工具（2026-09-13）──
    tool_registry.register(
        ToolMetadata(
            name="sec_filings",
            description=(
                "查美股公司在 SEC EDGAR 最近的申報：8-K 重大事件、10-Q／10-K 財報、"
                "Form 4 內部人交易、13D／13G 大股東、S-1，每筆附官方連結。"
            ),
            input_schema={
                "symbol": "str (美股代號)",
                "limit": "int (optional, default 10)",
                "all_forms": "bool (optional)",
            },
            handler=sec_filings,
            allowed_agents=["us_stock", "cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_btc_network_status",
            description=(
                "比特幣網路即時狀態：建議手續費（sat/vB）、mempool 壅塞、區塊高度、"
                "難度調整、下一次減半。免金鑰（mempool.space）。"
            ),
            input_schema={},
            handler=get_btc_network_status,
            allowed_agents=["crypto", "cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )

    tool_registry.register(
        ToolMetadata(
            name="get_my_scorecard",
            description=(
                "本人的判斷成績：帳本交易到期用收盤價對答案、扣同市場大盤後的命中率與超額報酬，"
                "分鏈上驗證／自報與市場。問「我最近判斷準嗎」用。"
            ),
            input_schema={},
            handler=get_my_scorecard,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )

    tool_registry.register(
        ToolMetadata(
            name="record_call",
            description=(
                "記一筆使用者自己的明確判斷（看多／看空某標的、幾天後對答案、可選目標價），"
                "到期用收盤價評分進判斷成績。使用者講「我看多 BTC 一個月」這種話時提案；"
                "會彈確認卡，核准才寫。"
            ),
            input_schema={
                "symbol": "str",
                "side": "str",
                "horizon_days": "int",
                "market": "str",
                "target_price": "float",
                "note": "str",
            },
            handler=record_call,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )

    # ── 唯讀記憶／skill 清單（低風險，無條件註冊）──
    # 2026-09-14 根絕「我沒有記憶」幻覺：之前本工具被 AGENT_SELF_MANAGE_ENABLED
    # （預設 false）擋住，旗標關閉時模型無法自查記憶，被問「有記住嗎」只能
    # 憑感覺答「我沒有長期記憶」。唯讀、無 HITL，設計文件即定位為 low risk。
    # remember(mode=delete) 的 key 取得路徑也依賴它，必須隨時可用。
    tool_registry.register(
        ToolMetadata(
            name="list_my_skills_memory",
            description=(
                "列出使用者的記憶（記住了什麼）與分析方法（官方 + 自訂 skills）。"
                "使用者問「你記得什麼」「我有什麼 skill」「剛剛有記住嗎」時呼叫；"
                "回答任何記憶相關問題前先用它查實際清單，不要憑感覺回答。唯讀、安全。"
            ),
            input_schema={
                "kind": "str (skill/memory/all, optional, default all)",
            },
            handler=list_my_skills_memory,
            allowed_agents=["cryptomind", "chat", "manager"],
            required_tier="free",
            risk_level="low",
        )
    )

    # ── Agent 自主管理 Skills / Memory（HITL-gated）──
    # docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md
    # 2026-10-06 起預設開啟（AGENT_SELF_MANAGE_ENABLED=false 關閉）；開著時 agent 可提議新建/修改/刪除
    # 自己的 skill，寫入前必過 consent gate（marker → interrupt）。
    if AGENT_SELF_MANAGE_ENABLED:
        tool_registry.register(
            ToolMetadata(
                name="propose_custom_skill",
                description=(
                    "提議建立/修改/刪除使用者的個人分析方法（custom skill）。"
                    "使用者明確要求，或重複相同分析需求適合固化成方法時呼叫。"
                    "不直接儲存——會彈出同意卡，使用者核准後才寫入。"
                    "官方 skill 是唯讀範本，不可修改；要客製化請新建個人版。"
                ),
                input_schema={
                    "mode": "str (create/update/delete, default create)",
                    "skill_name": "str",
                    "description": "str",
                    "trigger_keywords": "str (comma-separated)",
                    "body": "str (method steps)",
                    "reason": "str (why, in user's language)",
                },
                handler=propose_custom_skill,
                allowed_agents=["cryptomind", "chat", "manager"],
                required_tier="free",
                risk_level="high",
            )
        )
    else:
        logger.info(
            "[Bootstrap] AGENT_SELF_MANAGE_ENABLED=false — propose_custom_skill "
            "not registered（list_my_skills_memory 為唯讀，不受旗標影響）"
        )

    # ── Register New Free Market Data Tools ──
    tool_registry.register(
        ToolMetadata(
            name="get_gas_fees",
            description="獲取 Ethereum 網路的即時 Gas 費用",
            input_schema={},
            handler=get_gas_fees,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_whale_alerts",
            description="獲取加密貨幣的大額鏈上轉帳（鯨魚交易）",
            input_schema={"symbol": "str", "min_value_usd": "int"},
            handler=get_whale_transactions,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_exchange_flow",
            description="獲取加密貨幣交易所的資金流向數據",
            input_schema={"symbol": "str"},
            handler=get_exchange_flow,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_staking_yield",
            description="獲取加密貨幣的質押年化收益率(APY)。支援 SOL、ETH、ADA、ATOM 等主流 PoS 代幣。",
            input_schema={"symbol": "str"},
            handler=get_staking_yield,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )

    # ── Register CoinGecko Market Cap Tool (Free) ──
    tool_registry.register(
        ToolMetadata(
            name="get_crypto_market_cap",
            description="獲取加密貨幣市值排行 Top 100，含價格、市值與 24h 漲跌（CoinGecko 免費 API）",
            input_schema={},
            handler=get_crypto_market_cap,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_dex_volume",
            description="查詢去中心化交易所交易量排行（DeFiLlama 免費 API）。顯示各 DEX 的 24h 與 7d 交易量。",
            input_schema={},
            handler=get_dex_volume,
            allowed_agents=["crypto", "manager"],
            required_tier="premium",
        )
    )

    # ── Register DexScreener Tools ──
    tool_registry.register(
        ToolMetadata(
            name="get_dex_pair_info",
            description="獲取 DEX 代幣對的詳細資訊（價格、流動性、交易量）。輸入代幣合約地址。",
            input_schema={"token_address": "str"},
            handler=get_dex_pair_info_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_trending_dex_pairs",
            description="搜索熱門 DEX 交易對（如 PEPE、WIF、DOGE）。輸入代幣名稱或符號。",
            input_schema={"query": "str"},
            handler=get_trending_dex_pairs_tool,
            allowed_agents=["crypto", "manager"],
        )
    )

    # ── Register Etherscan On-chain Tools（多鏈：Ethereum/BSC/Polygon/Arbitrum 等 11 鏈）──
    # chain_id 預設 1（Ethereum mainnet）。其他鏈：BSC=56, Polygon=137, Arbitrum=42161,
    # Optimism=10, Base=8453, Avalanche=43114, Fantom=250, zkSync=324, Scroll=534352, Linea=59144。
    tool_registry.register(
        ToolMetadata(
            name="get_eth_balance",
            description=(
                "查詢 EVM 地址的原生代幣餘額（Ethereum/BSC/Polygon/Arbitrum 等）。"
                "輸入 0x 開頭的 42 字符地址。非 Ethereum 查詢需傳 chain_id（如 BSC=56, Polygon=137）。"
            ),
            input_schema={
                "address": "str",
                "chain_id": "int (optional, default 1=Ethereum)",
            },
            handler=get_eth_balance_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_erc20_token_balance",
            description=(
                "查詢 EVM 地址的 ERC20 代幣餘額（Ethereum/BSC/Polygon/Arbitrum 等）。"
                "需要錢包地址和代幣合約地址。非 Ethereum 查詢需傳 chain_id。"
            ),
            input_schema={
                "address": "str",
                "contract_address": "str",
                "chain_id": "int (optional, default 1=Ethereum)",
            },
            handler=get_erc20_token_balance_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_address_transactions",
            description=(
                "查詢 EVM 地址的最近交易記錄（Ethereum/BSC/Polygon/Arbitrum 等）。"
                "可追蹤資金流向。非 Ethereum 查詢需傳 chain_id。"
            ),
            input_schema={
                "address": "str",
                "limit": "int (optional)",
                "chain_id": "int (optional, default 1=Ethereum)",
            },
            handler=get_address_transactions_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_contract_info",
            description=(
                "查詢 EVM 智能合約的基本資訊（創建者、代幣資訊等，支援多鏈）。"
                "非 Ethereum 查詢需傳 chain_id。"
            ),
            input_schema={
                "contract_address": "str",
                "chain_id": "int (optional, default 1=Ethereum)",
            },
            handler=get_contract_info_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_eth_price_etherscan",
            description="從 Etherscan 獲取 ETH 即時價格。",
            input_schema={},
            handler=get_eth_price_etherscan_tool,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )

    # ── Register TON 工具（唯讀、低風險）──
    #   平台完全退出交換鏈路 — 不提供個人化報價/兌換計算/交易引導。
    #   Agent 定位改為「分析地址、代幣與支付風險的唯讀安全助手」。
    tool_registry.register(
        ToolMetadata(
            name="get_ton_balance",
            description=(
                "查詢 TON 錢包地址的 TON 餘額與鏈上狀態。"
                "查「我錢包」時可不傳 address（自動用登入身份）；"
                "查其他錢包才傳 TON 地址（EQ... 或 UQ... 開頭）。"
                "用於回答「我錢包有多少 TON」「這個地址有多少 TON」。"
            ),
            input_schema={"address": "str"},
            handler=get_ton_balance,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_ton_jetton_balances",
            description=(
                "查詢 TON 錢包持有的所有 jetton 代幣餘額（USDt/NOT/DOGS 等），"
                "含 USD/TON 估值與官方驗證狀態。"
                "查「我錢包」時可不傳 address（自動用登入身份）；"
                "查其他錢包才傳 TON 地址（EQ... 或 UQ... 開頭）。"
                "用於回答「我錢包有哪些幣、各值多少錢」「我有多少 USDt」。"
                "搭配 get_ton_balance（原生 TON）可算出錢包總資產。"
            ),
            input_schema={"address": "str"},
            handler=get_ton_jetton_balances,
            allowed_agents=["crypto", "manager"],
        )
    )
    # ── 投資帳本（2026-08-21 design，DANNY Approve；2026-08-20 金流 HITL）──
    # Propose → Confirm → Commit：record_entry 只提案（回傳 __needs_consent__
    # marker），claw_loop 彈確認卡，使用者核准後才寫入（金流安全控管）。
    tool_registry.register(
        ToolMetadata(
            name="record_entry",
            description=(
                "Propose an entry in the user's unified ledger (expense / income /"
                " trade). Call this for ANY amount or financial transaction the"
                " user mentions — e.g. lunch, salary, buying/selling crypto,"
                " stocks (TW/US/HK/JP), forex, or commodities. A confirmation"
                " card is shown; nothing is saved until the user approves."
                " Examples: 'lunch 250' → expense 250 TWD; 'bought 0.5 BTC at"
                " 61200' → trade BTC 0.5 @61200 USD; '2330.TW 2 lots at 912' →"
                " trade quantity=2 price=912 TWD; futures with leverage →"
                " instrument_type='futures', direction, leverage."
            ),
            input_schema={
                "entry_type": "str (trade/expense/income)",
                "symbol": "str",
                "market": "str",
                "amount": "float",
                "currency": "str",
                "quantity": "float",
                "side": "str (buy/sell)",
                "instrument_type": "str",
                "direction": "str",
                "leverage": "float",
                "category": "str",
                "fee": "float",
                "note": "str",
            },
            handler=record_entry,
            allowed_agents=["manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="query_ledger",
            description=(
                "Query the user's unified ledger (expenses, income, trades) with"
                " filters (type/category/symbol/keyword/days). Use for questions"
                " like 'how much did I spend this month', 'my food spending',"
                " 'show my BTC trades'. Never answer ledger questions from"
                " memory — always call this tool."
            ),
            input_schema={
                "entry_type": "str",
                "category": "str",
                "symbol": "str",
                "search": "str",
                "days": "int",
                "limit": "int",
            },
            handler=query_ledger,
            allowed_agents=["manager"],
        )
    )
    # ── 帳本單筆刪改（2026-08-25；Propose → Confirm → Commit，同 record_entry）──
    # Web UI 已有刪改；這裡把能力補給 agent：只提案，核准後 soft delete / update。
    tool_registry.register(
        ToolMetadata(
            name="delete_ledger_entry",
            description=(
                "Propose deleting ONE entry from the user's unified ledger"
                " (soft delete). Only PROPOSES — a confirmation card is shown"
                " and nothing is deleted until the user approves. Find the"
                " entry's [#id] with query_ledger first. Use ONLY when the"
                " user explicitly asks to remove a recorded entry"
                " (duplicate / mistake)."
            ),
            input_schema={"entry_id": "int"},
            handler=delete_ledger_entry,
            allowed_agents=["manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="update_ledger_entry",
            description=(
                "Propose editing ONE entry (amount/currency/category/note) in"
                " the user's unified ledger. Only PROPOSES — a confirmation"
                " card is shown and nothing changes until the user approves."
                " Pass only the fields to change. Find the entry's [#id] with"
                " query_ledger first. Use ONLY for corrections the user"
                " explicitly requests."
            ),
            input_schema={
                "entry_id": "int",
                "amount": "float",
                "currency": "str",
                "category": "str",
                "note": "str",
            },
            handler=update_ledger_entry,
            allowed_agents=["manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_portfolio_pnl",
            description=(
                "Calculate the user's portfolio profit/loss across all"
                " investment positions (weighted-average cost, live prices)."
                " Use when the user asks 'am I profitable', 'my PnL',"
                " 'my performance this month'."
            ),
            input_schema={},
            handler=get_portfolio_pnl,
            allowed_agents=["manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_my_wallet_overview",
            description=(
                "查詢 TON 錢包的完整資產總覽：原生 TON 餘額 + 所有 jetton 餘額"
                "（USDt/NOT/DOGS 等，含 USD/TON 估值）+ 錢包總資產 USD 估值。"
                "查「我錢包」時可不傳 address（自動用登入身份）；查其他錢包才傳"
                " TON 地址（EQ... 或 UQ... 開頭）。"
                "用於回答「我錢包總共多少錢」「我有哪些資產」。"
            ),
            input_schema={"address": "str"},
            handler=get_my_wallet_overview,
            allowed_agents=["crypto", "manager"],
        )
    )
    # ── TON jetton 安全檢測（路線 B，docs/plans/2026-08-11-ton-scam-detection-strengthen-design.md）──
    # 與 GoPlus check_token_security 對稱——GoPlus 不支援 TON，用 TonAPI 補上。
    # 註冊成 agent 工具後，claw_loop 的 _build_scam_evidence 會偵測它並彈詐騙證據卡。
    tool_registry.register(
        ToolMetadata(
            name="assess_jetton_safety",
            description=(
                "查詢 TON jetton 的鏈上安全訊號（白名單驗證 / 持有人數 / admin 權限）。"
                "輸入 TON jetton master 地址（EQ/UQ 開頭）。完全免費、無需金鑰。"
                "這是 TON 專屬安全檢測——遇到 TON 地址用此工具，遇到 EVM 地址（0x）用 check_token_security。"
            ),
            input_schema={"address": "str"},
            handler=assess_jetton_safety_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="check_token_security",
            description=(
                "檢測代幣合約的安全風險（蜂蜜罐 honeypot、rug pull 前兆、"
                "隱藏 owner、可改稅率等）。輸入代幣合約地址即可獲得完整安全報告。"
                "完全免費、無需金鑰。支援 Ethereum、BSC、Arbitrum 等 11+ 鏈。"
            ),
            input_schema={"contract_address": "str", "chain_id": "int (optional)"},
            handler=check_token_security_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="check_address_safety",
            description=(
                "檢測地址是否涉及惡意行為（釣魚、洗錢、制裁、混幣器、竊盜等）。"
                "需自帶 GoPlus API 金鑰。"
            ),
            input_schema={"address": "str", "chain_id": "int (optional)"},
            handler=check_address_safety_tool,
            allowed_agents=["crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_cmc_quote",
            description="使用 CoinMarketCap 查詢加密貨幣即時行情（價格、市值、漲跌幅、排名）。需自帶 CMC 金鑰。",
            input_schema={
                "symbol": "str (如 BTC、ETH)",
                "convert": "str (optional, 預設 USD)",
            },
            handler=get_cmc_quote_tool,
            allowed_agents=["crypto", "chat", "manager"],
        )
    )

    # ── Register Commodity Tools ──
    tool_registry.register(
        ToolMetadata(
            name="get_commodity_price",
            description=(
                "查詢大宗商品即時價格。**支援品項**：黃金(gold)、白銀(silver)、"
                "原油/石油(oil)、天然氣(natural_gas)、銅(copper)。"
                "**使用時機**：當用戶問「黃金」「金價」「oil」「原油」「白銀」"
                "「銅價」「天然氣」等大宗商品價格時，用此工具。"
                "commodity 參數接受中文或英文（如 黃金=gold, 白銀=silver, 原油=oil）。"
            ),
            input_schema={"commodity": "str (gold, silver, oil, natural_gas, copper)"},
            handler=get_commodity_price_tool,
            allowed_agents=["commodity", "crypto", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_commodity_futures_price",
            description="查詢商品期貨價格（WTI原油、布蘭特原油、黃金期貨、白銀期貨等）。",
            input_schema={
                "futures_type": "str (crude_oil, brent_oil, gold, silver, natural_gas)"
            },
            handler=get_commodity_futures_tool,
            allowed_agents=["commodity", "crypto", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_all_commodities_prices",
            description="獲取所有主要大宗商品價格一覽表。",
            input_schema={},
            handler=get_all_commodities_prices_tool,
            allowed_agents=["commodity", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_gold_silver_ratio",
            description="獲取金銀比（Gold-Silver Ratio），重要的市場情緒指標。",
            input_schema={},
            handler=get_gold_silver_ratio_tool,
            allowed_agents=["commodity", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_oil_price_analysis",
            description="獲取原油價格綜合分析（WTI vs 布蘭特原油比較）。",
            input_schema={},
            handler=get_oil_analysis_tool,
            allowed_agents=["commodity", "manager"],
        )
    )

    # ── Register Forex Tools ──
    tool_registry.register(
        ToolMetadata(
            name="get_forex_rate",
            description="查詢外匯即時匯率（USD/TWD、USD/JPY、EUR/USD等主要貨幣對）。",
            input_schema={"pair": "str (如 USD/TWD、EUR/USD)"},
            handler=get_forex_rate_tool,
            allowed_agents=["forex", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_all_forex_rates",
            description="獲取所有主要貨幣對的即時匯率一覽表。",
            input_schema={},
            handler=get_all_forex_rates_tool,
            allowed_agents=["forex", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_usd_twd_rate",
            description="查詢美元/台幣即時匯率（專用快捷工具）。",
            input_schema={},
            handler=get_usd_twd_rate_tool,
            allowed_agents=["forex", "chat", "tw_stock", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_central_bank_rates",
            description="獲取主要央行利率（Fed、ECB、BOJ、台灣央行）。需自帶 FRED 金鑰。",
            input_schema={},
            handler=get_central_bank_rates_tool,
            allowed_agents=["forex", "manager", "chat", "crypto"],
        )
    )

    # ── Register Universal Symbol Resolver ──
    tool_registry.register(
        ToolMetadata(
            name="resolve_symbol",
            description=(
                "將資產名稱/代號解析為標準交易代號。**跨市場通用**：支援 "
                "crypto/us/tw/hk/jp/kr/in。使用時機：看到不認識或無法確認歸屬的"
                "代號時（例如 SNXX、PEPE、2330），**必須先呼叫此工具確認**，"
                "不要憑猜測直接用其他工具。market=auto 預設走 crypto，若查不到"
                "請改試 market=us/tw/hk 等其他市場（例如英文代號極可能是美股）。"
            ),
            input_schema={
                "query": "str (代號或名稱，非 tw 市場建議用英文)",
                "market": "str (auto/crypto/us/tw/hk/jp/kr/in，預設 auto)",
            },
            handler=resolve_symbol,
            allowed_agents=["crypto", "chat", "manager"],
            role="symbol_lookup",
            priority=130,
        )
    )

    # ── Register Global Stock Tools (HK/JP/KR/IN) ──
    tool_registry.register(
        ToolMetadata(
            name="global_stock_price",
            description="查詢全球股市即時價格（港股/日股/韓股/印股）。market: hk/jp/kr/in",
            input_schema={"symbol": "str", "market": "str (hk/jp/kr/in)"},
            handler=global_stock_price_tool,
            allowed_agents=["global_stock", "chat", "manager"],
            role="market_lookup",
            priority=100,
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="global_stock_technical",
            description="計算全球股市技術指標（港股/日股/韓股/印股）。RSI/MACD/MA20/MA50/52W",
            input_schema={"symbol": "str", "market": "str (hk/jp/kr/in)"},
            handler=global_stock_technical_tool,
            allowed_agents=["global_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="global_stock_fundamentals",
            description="取得全球股市基本面（港股/日股/韓股/印股）。PE/PB/Beta/股息率/EPS/分析師目標",
            input_schema={"symbol": "str", "market": "str (hk/jp/kr/in)"},
            handler=global_stock_fundamentals_tool,
            allowed_agents=["global_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="global_stock_news",
            description="取得全球股市最新新聞（港股/日股/韓股/印股）",
            input_schema={
                "symbol": "str",
                "market": "str (hk/jp/kr/in)",
                "limit": "int",
            },
            handler=global_stock_news_tool,
            allowed_agents=["global_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="global_stock_snapshot",
            description="一次取得全球股市完整快照（港股/日股/韓股/印股）：價格+技術指標+基本面+新聞。適合全面分析。",
            input_schema={"symbol": "str", "market": "str (hk/jp/kr/in)"},
            handler=global_stock_snapshot_tool,
            allowed_agents=["global_stock", "manager"],
        )
    )

    # ── Register Economic Tools ──
    tool_registry.register(
        ToolMetadata(
            name="get_market_indices",
            description="獲取美股主要市場指數（S&P 500、道瓊、那斯達克、VIX恐慌指數）。",
            input_schema={},
            handler=get_market_indices_tool,
            allowed_agents=["economic", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_vix_index",
            description="獲取 VIX 恐慌指數詳細資訊和市場情緒判讀。",
            input_schema={},
            handler=get_vix_index_tool,
            allowed_agents=["economic", "chat", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_sp500_performance",
            description="獲取 S&P 500 指數詳細表現和各期間報酬。",
            input_schema={},
            handler=get_sp500_performance_tool,
            allowed_agents=["economic", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_sector_performance",
            description="獲取美股 11 大板塊表現（科技、金融、能源等）。",
            input_schema={},
            handler=get_sector_performance_tool,
            allowed_agents=["economic", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="get_economic_calendar",
            description="獲取近期重要經濟事件行事曆（利率決議、CPI、非農等）。",
            input_schema={},
            handler=get_economic_calendar_tool,
            allowed_agents=["economic", "manager"],
        )
    )

    # ── Register TW Stock Tools ──
    tool_registry.register(
        ToolMetadata(
            name="tw_stock_price",
            description="獲取台股即時價格和近期 OHLCV",
            input_schema={"ticker": "str"},
            handler=tw_price,
            allowed_agents=["tw_stock", "chat"],
            role="market_lookup",
            priority=100,
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_technical_analysis",
            description="計算台股技術指標 RSI/MACD/KD/MA",
            input_schema={"ticker": "str"},
            handler=tw_technical,
            allowed_agents=["tw_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_fundamentals",
            description="獲取台股基本面資料（P/E, EPS, ROE）",
            input_schema={"ticker": "str"},
            handler=tw_fundamentals_tool,
            allowed_agents=["tw_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_institutional",
            description="獲取台股三大法人籌碼資料",
            input_schema={"ticker": "str"},
            handler=tw_institutional_tool,
            allowed_agents=["tw_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_news",
            description="獲取台股相關新聞（Google News RSS）",
            input_schema={"ticker": "str", "company_name": "str"},
            handler=tw_news_tool,
            allowed_agents=["tw_stock"],
        )
    )
    # ── Register TWSE OpenAPI Tools ──
    tool_registry.register(
        ToolMetadata(
            name="tw_major_news",
            description="獲取台股上市公司今日重大訊息（TWSE 官方數據）",
            input_schema={"limit": "int (optional, default 10)"},
            handler=tw_major_news_tool,
            allowed_agents=["tw_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_pe_ratio",
            description="獲取台股個股本益比(P/E)、殖利率(Dividend Yield)、股價淨值比(P/B)",
            input_schema={"code": "str (股票代號)"},
            handler=tw_pe_ratio_tool,
            allowed_agents=["tw_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_monthly_revenue",
            description="獲取台股月營收資料，含月增率與年增率",
            input_schema={"code": "str (股票代號，空白為全市場)"},
            handler=tw_monthly_revenue_tool,
            allowed_agents=["tw_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_dividend",
            description="獲取台股股利分派資訊（現金股利、配股、除息日）",
            input_schema={"code": "str (股票代號，空白為全市場)"},
            handler=tw_dividend_tool,
            allowed_agents=["tw_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_foreign_top20",
            description="獲取外資及陸資持股台股前20名，含持股比率與可投資上限",
            input_schema={},
            handler=tw_foreign_top20_tool,
            allowed_agents=["tw_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_stock_snapshot",
            description="一次取得台股完整快照：即時價格 + 技術指標(RSI/MACD/KD/均線) + 基本面(PE/EPS/ROE) + 三大法人籌碼 + 最新新聞(5則)。適合全面分析。",
            input_schema={"ticker": "str (股票代號或公司名稱)"},
            handler=tw_stock_snapshot_tool,
            allowed_agents=["tw_stock", "manager"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="tw_market_index",
            description="獲取台股大盤指數：加權指數(TAIEX)與櫃買指數(OTC)，含漲跌點數與百分比。用戶問整體市場而非個股時使用。",
            input_schema={},
            handler=tw_market_index_tool,
            allowed_agents=["tw_stock", "manager"],
        )
    )

    # ── Register US Stock Tools ──
    tool_registry.register(
        ToolMetadata(
            name="us_stock_price",
            description="獲取美股即時價格數據（15分鐘延遲，Yahoo Finance）",
            input_schema={"symbol": "str"},
            handler=us_stock_price,
            allowed_agents=["us_stock", "chat"],
            role="market_lookup",
            priority=100,
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_technical_analysis",
            description="計算美股技術指標：RSI、MACD、布林帶、均線",
            input_schema={"symbol": "str"},
            handler=us_technical_analysis,
            allowed_agents=["us_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_fundamentals",
            description="獲取美股基本面：P/E、EPS、ROE、市值、股息率",
            input_schema={"symbol": "str"},
            handler=us_fundamentals,
            allowed_agents=["us_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_earnings",
            description="獲取美股財報數據和財報日曆",
            input_schema={"symbol": "str"},
            handler=us_earnings,
            allowed_agents=["us_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_news",
            description="獲取美股相關最新新聞",
            input_schema={"symbol": "str", "limit": "int (optional, default 5)"},
            handler=us_news,
            allowed_agents=["us_stock"],
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_institutional_holders",
            description="獲取美股機構持倉數據",
            input_schema={"symbol": "str"},
            handler=us_institutional_holders,
            allowed_agents=["us_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_insider_transactions",
            description="獲取美股內部人交易記錄",
            input_schema={"symbol": "str"},
            handler=us_insider_transactions,
            allowed_agents=["us_stock"],
            required_tier="premium",
        )
    )
    tool_registry.register(
        ToolMetadata(
            name="us_stock_snapshot",
            description="一次取得美股完整快照：即時價格 + 技術指標(RSI/MACD/MA/布林帶) + 基本面(PE/EPS/ROE/市值) + 財報數據 + 最新新聞(5則)。適合全面分析。",
            input_schema={"symbol": "str (美股代號或公司名稱)"},
            handler=us_stock_snapshot_tool,
            allowed_agents=["us_stock", "manager"],
        )
    )

    # ── Register ToolResultCompactor retrieval tool ──
    from langchain_core.tools import tool as lc_tool

    from core.agents.tool_compactor import retrieve_tool_result as _retrieve_fn

    _owner_id = user_id  # capture per-user scope in closure

    @lc_tool
    def tool_result_retrieve(uuid: str) -> str:
        """Retrieve the full content of a previously compacted tool result by its UUID key."""
        return _retrieve_fn(uuid, requester_id=_owner_id)

    tool_registry.register(
        ToolMetadata(
            name="tool_result_retrieve",
            description="Retrieve the full content of a previously compacted tool result by its UUID key.",
            input_schema={"uuid": "str"},
            handler=tool_result_retrieve,
            allowed_agents=[],  # [] = available to all agents
            required_tier="free",
        )
    )
    _align_required_tiers_with_seed(tool_registry)
    return tool_registry


def bootstrap(
    llm_client,
    web_mode: bool = False,
    language: str = "zh-TW",
    user_tier: str = "free",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    key_fingerprint: str = "",
    display_name: Optional[str] = None,
    wallet_address: Optional[str] = None,
    evm_address: Optional[str] = None,
    user_model_preference: Optional[str] = None,
    router_llm_client=None,
) -> ManagerAgent:
    user_tier = normalize_membership_tier(user_tier)
    PromptRegistry.load()
    PromptRegistry.set_user_language(language)

    # Preload skill catalog (類 CLAW 模式 — 方法知識預載)
    try:
        from .skill_loader import get_skill_loader

        get_skill_loader().load_all()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[Bootstrap] SkillLoader preload failed (non-fatal): {e}")

    lang_llm = LanguageAwareLLM(llm_client, language)

    # Shared TokenTracker — all agents report to the same tracker so the
    # Manager can inspect total cost across the entire request lifecycle.
    token_tracker = TokenTracker()

    cache_key = _manager_cache_key(user_id, session_id, key_fingerprint, language)
    # bootstrap 走 run_sync 執行緒池，多個請求會同時讀寫快取——查／刪／移到尾端
    # 要在鎖內做完（否則 KeyError 或迭代中被改）；建 manager 很貴，不持鎖。
    with _manager_cache_lock:
        existing_entry = _manager_cache.get(cache_key)

        if existing_entry is not None:
            existing, created_at = existing_entry
            if time.time() - created_at >= CACHE_TTL_SECONDS:
                _manager_cache.pop(cache_key, None)
                existing_entry = None
            elif existing.session_id != session_id:
                # Cached agent was compiled with a different session_id checkpointer.
                # Invalidate to avoid stale checkpointer key mismatch on ainvoke.
                _manager_cache.pop(cache_key, None)
                existing_entry = None
            else:
                _manager_cache.move_to_end(cache_key)

    if existing_entry is not None:
        existing, _ = existing_entry
        existing.llm = lang_llm
        existing._user_model_preference = user_model_preference
        # Routed clients inherit credentials/model options from the current LLM.
        # A reused manager must not retain clients built for a previous request.
        existing._routed_llm_cache = {}
        existing._router_llm_client = router_llm_client
        existing.user_tier = user_tier
        existing.user_id = user_id or "anonymous"
        # 身份 attr 跟 user_tier 一樣是 per-request 可變的，cache-hit 時 re-apply
        # （不放入 cache key，避免使用者改暱稱後 cache 失效）
        existing.display_name = display_name
        existing.wallet_address = wallet_address
        if hasattr(existing, "tool_access_resolver"):
            existing.tool_access_resolver.update_scope(user_tier, existing.user_id)
        if session_id:
            existing.session_id = session_id
            existing._memory_store = None
        for agent in existing.agent_registry._agents.values():
            if hasattr(agent, "llm"):
                agent.llm = lang_llm
            if hasattr(agent, "user_tier"):
                agent.user_tier = user_tier
            if hasattr(agent, "user_id"):
                agent.user_id = user_id
            # Principal：把個人化身份也套用到每個 agent
            if hasattr(agent, "display_name"):
                agent.display_name = display_name
            if hasattr(agent, "wallet_address"):
                agent.wallet_address = wallet_address
            if hasattr(agent, "evm_address"):
                agent.evm_address = evm_address
        return existing

    agent_registry = AgentRegistry()
    tool_registry = build_tool_registry(user_id)

    # ── Register MCP tools（可選，MCP_ENABLED=1 才載入）──
    # 把外部 MCP server 的 tools 接進 registry。治理映射：MCP tool 的風險覆寫先
    # 註冊進 _TOOL_RISK_LEVELS 單一真相來源，這樣下方統一的 risk_level 注入才會
    # 正確讀到（high 必須顯式覆寫，絕不自動升 high，避免繞過 Consent Gate）。
    # 見 core/tools/mcp_loader.py 與 docs/MCP_INTEGRATION_ASSESSMENT.md。
    try:
        from core.database.tools import register_mcp_risk_overrides
        from core.tools.mcp_loader import load_mcp_tools_sync

        mcp_entries = load_mcp_tools_sync()
        if mcp_entries:
            # 把風險覆寫合併進單一真相來源（既有 tool 不被覆蓋）
            register_mcp_risk_overrides(
                {
                    getattr(t, "name", ""): r
                    for t, r, _allowed, _tier in mcp_entries
                    if r != "low"
                }
            )
        for mcp_tool, _mcp_risk, mcp_allowed, mcp_tier in mcp_entries:
            tool_registry.register(
                ToolMetadata(
                    name=getattr(mcp_tool, "name", "mcp_tool"),
                    description=getattr(mcp_tool, "description", "")[:500],
                    input_schema={},
                    handler=mcp_tool,
                    allowed_agents=mcp_allowed,
                    required_tier=mcp_tier,
                    # risk_level 由下方統一注入（get_tool_risk_level）決定
                )
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[Bootstrap] MCP tools 載入失敗（非致命）: {e}")

    # ── Create Agents ──

    # Legacy agents (hidden from LLM classify; kept for backward compatibility via direct name lookup)
    # tech = TechAgent(lang_llm, tool_registry)
    # agent_registry.register(tech, AgentMetadata(
    #     name="technical",
    #     display_name="Tech Agent (Legacy)",
    #     description="[Legacy] 加密貨幣技術分析。新查詢請使用 crypto agent。",
    #     capabilities=["RSI", "MACD", "MA", "technical analysis"],
    #     allowed_tools=["technical_analysis", "price_data", "get_crypto_price"],
    #     priority=1,
    #     hidden=True,
    # ))

    # news = NewsAgent(lang_llm, tool_registry)
    # agent_registry.register(news, AgentMetadata(
    #     name="news",
    #     display_name="News Agent (Legacy)",
    #     description="[Legacy] 加密貨幣新聞。新查詢請使用 crypto agent。",
    #     capabilities=["news", "新聞"],
    #     allowed_tools=["google_news", "aggregate_news", "web_search"],
    #     priority=1,
    #     hidden=True,
    # ))

    agent = CryptoMindAgent(
        lang_llm,
        tool_registry,
        user_tier=user_tier,
        user_id=user_id,
        token_tracker=token_tracker,
        display_name=display_name,
        wallet_address=wallet_address,
        evm_address=evm_address,
    )
    agent_registry.register(
        agent,
        AgentMetadata(
            name="cryptomind",
            display_name="CryptoMind Agent",
            description="CLAW-style single agent — all markets, all tools.",
            capabilities=["all"],
            priority=0,
        ),
    )
    manager = ManagerAgent(
        llm_client=lang_llm,
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        web_mode=web_mode,
        user_tier=user_tier,
        user_id=user_id,
        session_id=session_id or "default",
        display_name=display_name,
        wallet_address=wallet_address,
    )
    # Share the same TokenTracker so manager sees combined cost
    manager._token_tracker = token_tracker
    manager._user_model_preference = user_model_preference
    # Router 模型可指派（2026-09-09 設計 B2）：API 層在 graph 前用對應
    # provider 金鑰建好的獨立 client——api_key 不進 graph state 的紅線不破。
    # None＝未設定，Router 跑使用者預設模型（現行為）。
    manager._router_llm_client = router_llm_client
    manager.language = language
    with _manager_cache_lock:
        while len(_manager_cache) >= MAX_CACHE_SIZE:
            _manager_cache.popitem(last=False)
        _manager_cache[cache_key] = (manager, time.time())

    # Register post-response hooks (once per process, idempotent)
    _register_post_response_hooks()

    # 把 tool_registry 內所有 tool name 注入動態註冊表，
    # 供 claw_loop._strip_tool_name_leaks 使用（取代硬編碼清單）。
    # 見 core/agents/tool_name_registry.py。
    try:
        from .tool_name_registry import register_tool_names

        # request_more_tools／read_more_result 不在 tool registry（它們不授權任何東西，只在該輪由 agent 臨時掛上：
        # 前者是工具櫥窗的出口、後者是分頁續讀），但模型可能把它們寫進回答文字，sanitizer 一樣要認得
        register_tool_names(
            set(tool_registry._tools.keys()) | {"request_more_tools", "read_more_result"}
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[Bootstrap] tool_name_registry 注入失敗（非致命）: {e}")

    # 注入 risk_level 到每個 ToolMetadata（Consent Gate 用）。
    # 從 core/database/tools.py 的 _TOOL_RISK_LEVELS 單一真相來源讀取，
    # 確保 DB catalog / runtime ToolMetadata / 前端三者一致。未列出者預設 'low'。
    try:
        from core.database.tools import get_tool_risk_level

        for name, meta in tool_registry._tools.items():
            meta.risk_level = get_tool_risk_level(name)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[Bootstrap] risk_level 注入失敗（非致命，預設 low）: {e}")

    return manager


# ── Manager Instance Cache (LRU + TTL) ──────────────────────────────────────────
_agent_class_cache: Dict[str, type] = {}

MAX_CACHE_SIZE = 100
CACHE_TTL_SECONDS = 3600

_manager_cache: OrderedDict[str, tuple[ManagerAgent, float]] = OrderedDict()
# _manager_cache 會被執行緒池裡的 bootstrap 與 API 端的 invalidate 同時讀寫
_manager_cache_lock = threading.Lock()


def _manager_cache_key(
    user_id: str,
    session_id: str = "default",
    key_fingerprint: str = "",
    language: str = "",
) -> str:
    return f"{user_id}:{session_id or 'default'}:{key_fingerprint}:{language}"


def get_manager_instances(user_id: str) -> list[ManagerAgent]:
    prefix = f"{user_id}:"
    now = time.time()
    result = []
    with _manager_cache_lock:
        expired_keys = []
        for key, (manager, created_at) in _manager_cache.items():
            if now - created_at >= CACHE_TTL_SECONDS:
                expired_keys.append(key)
                continue
            if key.startswith(prefix):
                result.append(manager)
        for key in expired_keys:
            del _manager_cache[key]
    return result


def get_manager_instance(
    user_id: str, session_id: Optional[str] = None
) -> Optional[ManagerAgent]:
    prefix = f"{user_id}:{session_id or ''}:" if session_id else f"{user_id}:"
    now = time.time()
    with _manager_cache_lock:
        for key in reversed(list(_manager_cache.keys())):
            if not key.startswith(prefix):
                continue
            manager, created_at = _manager_cache[key]
            if now - created_at >= CACHE_TTL_SECONDS:
                del _manager_cache[key]
                continue
            _manager_cache.move_to_end(key)
            return manager
    return None


def invalidate_manager_cache(user_id: str, session_id: Optional[str] = None) -> None:
    # session_id 指定時用 prefix 搜尋（跨 fingerprint/language）。
    # 注意：不能用 _manager_cache_key(user_id, session_id) 組單一 key——
    # production 的 cache key 含真實 key_fingerprint（sha256 前 8 碼）與 language，
    # 那兩欄為空字串時才 match 得到，等於永遠 pop 不到（既有 bug，此處修正）。
    prefix = f"{user_id}:{session_id}:" if session_id is not None else f"{user_id}:"
    with _manager_cache_lock:
        for key in [k for k in _manager_cache.keys() if k.startswith(prefix)]:
            _manager_cache.pop(key, None)
