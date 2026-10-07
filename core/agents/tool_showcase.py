"""工具櫥窗——依題目挑「代表工具」給模型看，其餘先藏起來，要用再開（DANNY 2026-10-06 的方向）。

背景（2026-10-06 實測）
-----------------------
金融題的第一次主呼叫要讀 86 個工具定義（約 3.6 萬字、1.3 萬 token）＋ 1.8 萬字 system prompt，
本機模型約 25 秒。這些工具大部分與當題無關：問台股時，美股／商品／外匯／帳本／行事曆…的定義都白讀了。
工具輸出被截在 2000 字也是同一個原因——context 被工具定義佔掉，沒有空間給資料。

做法
----
1. **規則（0 ms、不送第三方）**：從題目認出市場家族（台股／美股／港日韓印／加密幣／商品／外匯／總經／鏈上安全），
   只展示那幾個家族的「代表工具」＋一組永遠在的核心工具（釐清、時間、符號解析、網搜、skill、記憶…）。
2. **寧可漏判，不可誤判**：認不出、混了多件事、題目帶「動作／個人」語境（記、提醒、我的持倉、帳本、記憶…）、
   跨家族超過三個——一律回 None＝照原本全工具。
3. **有出口**：縮窄的那一輪多一個小工具 ``request_more_tools``。模型發現清單裡沒有需要的工具就呼叫它，
   之後的每次模型呼叫改回完整清單（同一輪、同一個對話脈絡，不重跑）。
   工具本身一直都在 ToolNode 裡（權限仍由原本的 tier／使用者勾選層把關），
   櫥窗只改「這次呼叫展示哪些定義給模型」——不是解鎖，也不是移除。

與 ``domain_gate`` 的分工：帳本題走那邊（硬縮池＋精簡 prompt）；這裡處理市場題（軟縮：只改展示）。
開關 ``TOOL_SHOWCASE_NARROWING``（預設開，false/0/no/off 關閉）。
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

REQUEST_MORE_TOOLS = "request_more_tools"

# 一次最多合併幾個家族；超過代表題目太雜，不縮
MAX_FAMILIES = 3
# 長訊息多半是多件事（貼文章、混著問），不縮
MAX_QUERY_CHARS = 120


def showcase_enabled() -> bool:
    """``TOOL_SHOWCASE_NARROWING``（預設開；false/0/no/off ＝ 回到每題展示全部工具）。"""
    return os.getenv("TOOL_SHOWCASE_NARROWING", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# ── 工具家族（名稱＝ tool registry／_TOOLS_SEED 的 tool_id；tests 會逐一對照）──────────
FAMILY_TOOLS: Dict[str, Tuple[str, ...]] = {
    "crypto": (
        "get_crypto_price",
        "technical_analysis",
        "get_futures_data",
        "get_fear_and_greed_index",
        "get_crypto_market_cap",
        "get_trending_tokens",
        "get_crypto_categories_and_gainers",
        "get_defillama_tvl",
        "get_token_supply",
        "get_token_unlocks",
        "get_btc_network_status",
        "aggregate_news",
        "google_news",
    ),
    "tw_stock": (
        "tw_stock_price",
        "tw_technical_analysis",
        "tw_fundamentals",
        "tw_institutional",
        "tw_news",
        "tw_major_news",
        "tw_pe_ratio",
        "tw_monthly_revenue",
        "tw_dividend",
        "tw_foreign_top20",
        "tw_stock_snapshot",
        "tw_market_index",
    ),
    "us_stock": (
        "us_stock_price",
        "us_technical_analysis",
        "us_fundamentals",
        "us_earnings",
        "us_news",
        "us_institutional_holders",
        "us_insider_transactions",
        "us_stock_snapshot",
        "sec_filings",
        "get_market_indices",
    ),
    "global_stock": (
        "global_stock_price",
        "global_stock_technical",
        "global_stock_fundamentals",
        "global_stock_news",
        "global_stock_snapshot",
    ),
    "commodity": (
        "get_commodity_price",
        "get_commodity_futures_price",
        "get_all_commodities_prices",
        "get_gold_silver_ratio",
        "get_oil_price_analysis",
    ),
    "forex": (
        "get_forex_rate",
        "get_all_forex_rates",
        "get_usd_twd_rate",
    ),
    "macro": (
        "get_market_indices",
        "get_vix_index",
        "get_sp500_performance",
        "get_sector_performance",
        "get_economic_calendar",
        "get_central_bank_rates",
    ),
    "onchain": (
        "check_token_security",
        "check_address_safety",
        "assess_jetton_safety",
        "get_contract_info",
        "get_dex_pair_info",
        "get_trending_dex_pairs",
        "get_dex_volume",
        "get_whale_alerts",
        "get_gas_fees",
        "get_staking_yield",
        "get_exchange_flow",
        "get_eth_balance",
        "get_erc20_token_balance",
        "get_address_transactions",
        "get_ton_balance",
        "get_ton_jetton_balances",
        "get_cmc_quote",
        "get_eth_price_etherscan",
    ),
}

# 永遠展示：釐清、時間錨點、符號解析、skill、記憶、自我管理——任何市場題都可能要，
# 而且 system prompt 裡有明講要用它們（少了會出現「prompt 叫我用、清單卻沒有」的矛盾）。
#
# 刻意**不含 web_search／fetch_url**（2026-10-06 本機真模型實測）：櫥窗裡缺了需要的工具時
# （例：問「台積電 ADR 價差」只展示台股家族），只要網搜還在，模型就不呼叫出口工具，
# 改用 web_search／fetch_url 硬湊——一題打了 18 次 LLM 才答完（55 次才停是另一次跑到上限）。
# 沒有網搜這條「看起來能做」的路，模型才會老實呼叫 request_more_tools 開完整清單。
CORE_TOOLS: Tuple[str, ...] = (
    "clarify",
    "get_current_time_taipei",
    "resolve_symbol",
    "load_skill",
    "load_knowledge",
    "remember",
    "list_my_skills_memory",
    "propose_custom_skill",
)

# ── 市場訊號（小寫、NFKC 後比對；ASCII 詞要有字邊界，避免 eth 命中 method）──────────────
_SIGNALS: Dict[str, Tuple[str, ...]] = {
    "tw_stock": (
        "台股", "台灣股", "台灣股市", "加權指數", "加权指数", "櫃買", "柜买", "證交所", "证交所",
        "上市櫃", "上柜", "三大法人", "外資", "外资", "投信", "融資融券", "融资融券", "除息", "除權息",
        "月營收", "月营收", "taiex",
        "台積電", "台积电", "鴻海", "鸿海", "聯發科", "联发科", "中華電", "中华电", "國泰金", "国泰金",
        "富邦金", "長榮", "长荣", "陽明", "阳明", "萬海", "万海", "廣達", "广达", "緯創", "纬创",
        "台達電", "台达电", "大立光", "中鋼", "中钢", "聯電", "联电", "日月光", "華碩", "华硕", "宏碁",
        "兆豐金", "兆丰金", "玉山金", "緯穎", "纬颖", "技嘉", "瑞昱", "元大台灣50", "0050", "0056",
        "00878", "00919", "2330", "2317", "2454", "2412", "2882", "2881", "2603", "2609", "2615",
        "2308", "2382", "3711", "2303", "2002", "1301", "1303", "2891", "2892", "2886", "2884",
        "2885", "2357", "2379", "2327", "2395", "3034",
    ),
    "us_stock": (
        "美股", "那斯達克", "納斯達克", "纳斯达克", "納指", "纳指", "道瓊", "道琼", "標普", "标普",
        "費半", "费半", "羅素", "罗素", "華爾街", "华尔街", "nasdaq", "dow jones", "s&p", "sp500",
        "10-k", "10-q", "8-k", "sec filing", "adr", "存託憑證", "存托凭证", "美國掛牌", "美国挂牌", "內部人交易", "内部人交易", "機構持股", "机构持股",
        "aapl", "msft", "nvda", "tsla", "amzn", "googl", "goog", "amd", "intc", "nflx",
        "avgo", "tsm", "baba", "mstr", "pltr", "qqq", "orcl",
        "蘋果公司", "苹果公司", "輝達", "辉达", "特斯拉", "微軟", "微软", "亞馬遜", "亚马逊",
        "谷歌", "臉書", "脸书", "網飛", "网飞",
    ),
    "global_stock": (
        "港股", "恆生", "恒生", "日股", "日經", "日经", "韓股", "韩股", "kospi", "印度股", "印股",
        "sensex", "nifty", "騰訊", "腾讯", "豐田", "丰田", "索尼", "任天堂", "軟銀", "软银",
        "三星電子", "三星电子", "海力士", "美團", "美团", "小米集團", "小米集团",
    ),
    "crypto": (
        "比特幣", "比特币", "以太幣", "以太币", "以太坊", "加密", "代幣", "代币", "山寨", "幣圈",
        "币圈", "幣價", "币价", "穩定幣", "稳定币", "狗狗幣", "狗狗币", "瑞波", "資金費率",
        "资金费率", "多空比", "恐慌貪婪", "恐慌与贪婪", "恐慌與貪婪", "永續合約", "永续合约",
        "defi", "tvl", "bitcoin", "ethereum", "solana", "dogecoin", "ripple", "cardano", "crypto",
        "btc", "eth", "bnb", "xrp", "doge", "ada", "avax", "ltc", "trx", "usdt", "usdc", "shib",
        "pepe", "fear and greed", "биткоин", "эфириум", "криптовалют",
    ),
    "commodity": (
        "黃金", "黄金", "金價", "金价", "白銀", "白银", "原油", "石油", "天然氣", "天然气", "銅價",
        "铜价", "大宗商品", "商品期貨", "商品期货", "布蘭特", "布兰特", "油價", "油价", "wti",
        "gold price", "silver", "crude oil", "brent", "золото", "нефть",
    ),
    "forex": (
        "匯率", "汇率", "外匯", "外汇", "美元兌", "美元兑", "美金", "日圓", "日元", "歐元", "欧元",
        "英鎊", "英镑", "人民幣匯", "人民币汇", "forex", "usd/", "eur/", "jpy/", "gbp/", "курс валют",
    ),
    "macro": (
        "聯準會", "联准会", "美聯儲", "美联储", "fomc", "升息", "降息", "cpi", "ppi", "通膨", "通胀",
        "非農", "非农", "失業率", "失业率", "gdp", "經濟日曆", "经济日历", "央行", "利率決議",
        "利率决议", "vix", "恐慌指數", "恐慌指数", "經濟數據", "经济数据", "the fed", "federal reserve",
    ),
    "onchain": (
        "鏈上", "链上", "地址", "合約地址", "合约地址", "智能合約", "智能合约", "蜜罐", "rug", "詐騙",
        "诈骗", "釣魚", "钓鱼", "安全嗎", "安全吗", "鯨魚", "鲸鱼", "巨鯨", "巨鲸", "gas", "dex",
        "流動性", "流动性", "質押", "质押", "staking", "swap", "onchain", "on-chain", "whale",
    ),
}

# 帶這些就不縮：使用者在「做事」或談「自己的」東西，工具需求不能靠猜
_DO_OR_PERSONAL = (
    "記", "记", "提醒", "新增", "加入", "加到", "加進", "加进", "刪除", "删除", "修改", "更新", "設定", "设置", "訂閱", "订阅",
    "帳本", "账本", "帐本", "損益", "损益", "我的", "我持", "我買", "我买", "我賣", "我卖", "我手上",
    "持倉", "持仓", "投資組合", "投资组合", "成績", "成绩", "行事曆", "行事历", "记忆", "記憶",
    "偏好", "技能", "skill", "memory", "remember", "kyc", "開戶", "开户", "錢包", "钱包", "綁定", "绑定",
    "remind", "my portfolio", "my wallet", "my ledger", "add to", "delete", "напомни", "мой ",
)

_HEX_ADDR_RE = re.compile(r"0x[0-9a-f]{6,}")
_TON_ADDR_RE = re.compile(r"\b[eu]q[a-z0-9_-]{40,}\b")


@dataclass(frozen=True)
class ShowcaseDecision:
    """櫥窗決策。``families`` 為空 ＝ 不確定 ＝ 照現況（展示全部工具）。"""

    families: Tuple[str, ...]
    reason: str

    @property
    def matched(self) -> bool:
        return bool(self.families)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().lower()


def _has_term(q: str, term: str) -> bool:
    """中文詞與含符號的詞（s&p、usd/、10-k）用子字串；純英數詞要有字邊界（eth 不該命中 method、gas 不該命中 gasoline）。"""
    if term.isascii() and term.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", q) is not None
    return term in q


def detect_families(q: str) -> List[str]:
    found: List[str] = []
    for family, terms in _SIGNALS.items():
        if any(_has_term(q, term) for term in terms):
            found.append(family)
    if _HEX_ADDR_RE.search(q) or _TON_ADDR_RE.search(q):
        if "onchain" not in found:
            found.append("onchain")
    return found


def decide_showcase(query: str) -> ShowcaseDecision:
    """純規則、無 I/O。回傳要展示的市場家族，或「不確定」。"""
    if not showcase_enabled():
        return ShowcaseDecision((), "disabled")
    q = _normalize(query)
    if not q:
        return ShowcaseDecision((), "empty")
    if len(q) > MAX_QUERY_CHARS:
        return ShowcaseDecision((), "too_long")
    if any(term in q for term in _DO_OR_PERSONAL):
        return ShowcaseDecision((), "action_or_personal")
    families = detect_families(q)
    if not families:
        return ShowcaseDecision((), "no_signal")
    if len(families) > MAX_FAMILIES:
        return ShowcaseDecision((), "too_many_families")
    return ShowcaseDecision(tuple(sorted(families)), "families:" + ",".join(sorted(families)))


def tool_names_for(families: Iterable[str]) -> Optional[List[str]]:
    """家族 → 要展示的工具名（含核心工具）；沒有任何有效家族回 None。"""
    names = set(CORE_TOOLS)
    matched = False
    for family in families:
        tools = FAMILY_TOOLS.get(family)
        if tools:
            matched = True
            names.update(tools)
    return sorted(names) if matched else None


# ── 展示層：只改「這次模型呼叫看到哪些工具定義」──────────────────────────────────────

try:  # langchain 的 middleware 基底；測試環境沒裝時退回純物件（同 router_effects）
    from langchain.agents.middleware import AgentMiddleware as _Base
except Exception:  # noqa: BLE001
    _Base = object  # type: ignore[assignment,misc]


def build_request_more_tools_tool() -> Any:
    """縮窄那一輪才掛的出口工具。回傳 LangChain tool；不在 registry／DB（它不授權任何東西）。"""
    from langchain_core.tools import tool as lc_tool

    @lc_tool(REQUEST_MORE_TOOLS)
    def request_more_tools(reason: str = "") -> str:
        """目前展示的工具不夠回答這題時，先呼叫這個取得完整工具清單（例如需要別的市場、鏈上、帳本、網路搜尋 web_search）。
        呼叫後完整工具清單會開放，請接著直接呼叫需要的工具。
        不要用不相干的工具硬湊；但清單內已有合適工具時不要呼叫。reason 用一句話說明缺什麼。"""
        return "已開放完整工具清單。請直接呼叫需要的工具來回答。"

    return request_more_tools


def _tool_name(tool: Any) -> Optional[str]:
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(tool, dict):
        value = tool.get("name") or (tool.get("function") or {}).get("name")
        return value if isinstance(value, str) else None
    return None


class ToolShowcaseMiddleware(_Base):  # type: ignore[misc]
    """每次模型呼叫只展示 ``show_names`` ＋ 出口工具；呼叫過出口工具後改回完整清單（隱藏出口本身）。"""

    def __init__(self, show_names: Iterable[str]) -> None:
        if _Base is not object:
            super().__init__()
        self.show_names = frozenset(show_names)

    @staticmethod
    def expanded(messages: Iterable[Any]) -> bool:
        for message in messages or ():
            if getattr(message, "type", None) == "tool" and getattr(message, "name", None) == REQUEST_MORE_TOOLS:
                return True
        return False

    def _request(self, request: Any) -> Any:
        tools = list(getattr(request, "tools", None) or [])
        if self.expanded(getattr(request, "messages", None)):
            shown = [t for t in tools if _tool_name(t) != REQUEST_MORE_TOOLS]
        else:
            shown = [
                t
                for t in tools
                if _tool_name(t) is None
                or _tool_name(t) in self.show_names
                or _tool_name(t) == REQUEST_MORE_TOOLS
            ]
        return request.override(tools=shown)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._request(request))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._request(request))
