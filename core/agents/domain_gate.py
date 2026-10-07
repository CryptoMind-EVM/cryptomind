"""題型閘門——零 LLM 的本機「型別化決策」（Jev 類做法）。

背景（2026-10-06 實測，docs/plans/2026-10-06-chat-latency-domain-gate.md）
------------------------------------------------------------------------
金融訊號一律走完整 ReAct，主呼叫的 prompt ＝ 86 個工具定義＋18.5K 字 system prompt ≈ 2.2 萬 token。
主機模型（Qwen3.5 系 recurrent 混合架構、--ctx-checkpoints 0）沒有 prefix 部分重用，
每一題的第一次主呼叫都要整份重讀約 25 秒；帳本題 30.9 s 裡有 25.5 s 花在這裡。

Router（LLM）已經能依 domains 縮工具池，但它預設關閉，而且金融訊號本來就「硬否決」直接跳過它——
也就是最常見的金融題反而拿不到 domain。這裡補的是那個缺口：**用純字串規則決定題型**，
延遲 0 ms、不送任何第三方、可單測；決定不了就回 None，行為與現況完全相同。

原則（與 triage.py 同一條）
--------------------------
寧可漏判，不可誤判。漏判＝照原本全工具跑（現況，沒有變差）；誤判＝模型少了需要的工具。
所以：
1. 只認訊號強、範圍窄的題型（目前只有「帳本」）；
2. 任何「想要市場資料／判斷預測／記憶／鏈上」的衝突詞一出現就不縮；
3. 誤判的最壞結果有出口：縮池那一輪 prompt 會帶一行說明，模型會請使用者另外詢問市場資料。
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

DOMAIN_LEDGER = "ledger"

# 帳本題的工具池：記／查／改／刪＋損益；clarify 與時間錨點是任何題型都可能要的。
# 名稱要與 tool registry 一致（tests/test_domain_gate.py 會對 _TOOLS_SEED 驗證）。
LEDGER_TOOLS = (
    "record_entry",
    "query_ledger",
    "update_ledger_entry",
    "delete_ledger_entry",
    "get_portfolio_pnl",
)
CORE_TOOLS = ("clarify", "get_current_time_taipei")


def domain_gate_enabled() -> bool:
    """``DOMAIN_GATE_NARROWING``（預設開）；false/0/no/off ＝ 回到全工具現況。"""
    return os.getenv("DOMAIN_GATE_NARROWING", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# ── 帳本訊號 ───────────────────────────────────────────────────────────────
# 明確的帳本詞：使用者就是在談自己的帳。
# （「記錄」「紀錄」單獨不算——「交易紀錄」「查詢紀錄」太泛，見 _FOLLOW_UP_RE）
_LEDGER_EXPLICIT = (
    "帳本",
    "帐本",
    "账本",
    "記帳",
    "记账",
    "記一筆",
    "记一笔",
    "收支",
    "記入",
    "记入",
    "ledger",
    "bookkeeping",
    "журнал учёта",
    "журнал учета",
)
# 泛用財務詞：「台積電收入多少」「蘋果資本支出」是市場題，不是帳本題。
# 必須同時有第一人稱／自己的時間範圍語境（我、我的、這個月、今天…）才算。
_LEDGER_GENERIC = (
    "支出",
    "收入",
    "花費",
    "花费",
    "開銷",
    "开销",
    "expense",
    "income",
    "spending",
    "расход",
    "доход",
)
_PERSONAL_MARKERS = (
    "我",
    "本月",
    "這個月",
    "这个月",
    "上個月",
    "上个月",
    "本週",
    "這週",
    "这周",
    "上週",
    "上周",
    "今天",
    "昨天",
    "今年",
    "my ",
    "this month",
    "last month",
    "today",
    "yesterday",
    "мои",
    "мой",
    "моя",
    "я ",
)

# ── 衝突詞：出現任何一個就不縮池（使用者要的可能不只是帳本）──────────────────
_CONFLICT_TERMS = (
    # 行情／分析／判斷
    "行情",
    "走勢",
    "走势",
    "技術",
    "技术",
    "分析",
    "預測",
    "预测",
    "判斷",
    "判断",
    "看多",
    "看空",
    "值得",
    "該不該",
    "该不该",
    "推薦",
    "推荐",
    "建議",
    "建议",
    "價格",
    "价格",
    "報價",
    "报价",
    "現價",
    "现价",
    "現在多少",
    "现在多少",
    "漲",
    "涨",
    "跌",
    "新聞",
    "新闻",
    "財報",
    "财报",
    "本益比",
    "市值",
    "k線",
    "k线",
    "指標",
    "指标",
    "風險",
    "风险",
    # 公司／標的：「蘋果公司資本支出」「台積電營收」是市場題
    "公司",
    "營收",
    "营收",
    "財務",
    "财务",
    "股",
    # 單字「幣」會誤殺「台幣」「外幣」「人民幣」的記帳句，只認加密貨幣的說法
    "加密",
    "代幣",
    "代币",
    "比特幣",
    "比特币",
    "以太",
    "幣價",
    "币价",
    "幣圈",
    "币圈",
    "穩定幣",
    "稳定币",
    "山寨",
    "etf",
    "crypto",
    "stock",
    # 鏈上／錢包／安全
    "鏈上",
    "链上",
    "地址",
    "錢包",
    "钱包",
    "合約",
    "合约",
    "安全",
    "詐騙",
    "诈骗",
    # 記憶／技能／平台本身
    "記憶",
    "记忆",
    "記住",
    "记住",
    "偏好",
    "技能",
    "skill",
    "memory",
    "remember",
    # 英文
    "price of",
    "analysis",
    "forecast",
    "should i",
    "worth",
    "news",
    "chart",
)

# 口語快記：消費／收入詞＋金額（「午餐250」「薪水 50000」）。與 claw_loop._ledger_hint_suffix 同一批詞，
# 但這裡要求整句很短，避免「午餐吃什麼 2 人份」這種閒聊被當記帳。
_QUICK_RECORD_RE = re.compile(
    r"^(今天|昨天|剛剛|刚刚)?(午餐|晚餐|早餐|早點|咖啡|奶茶|計程車|車費|油錢|房租|水電|薪水|工資|薪資|獎金|紅包)"
    r"\s*[:：]?\s*\d+(\.\d+)?\s*(元|塊|块|twd|nt\$|\$)?$"
)

# 「記錄了嗎」「有記到嗎」這類短追問：自己不帶帳本詞，要看最近對話是不是真的在談帳本
_FOLLOW_UP_RE = re.compile(
    r"^(那|剛剛|剛才|刚刚|刚才)?.{0,6}(記|紀|记|纪)(錄|录|到|了|入)+(了|到|進去|进去)?(嗎|吗|沒|没|吧|呢)?$"
)
_HISTORY_LEDGER_TERMS = (
    "帳本",
    "帐本",
    "账本",
    "記入",
    "记入",
    "記帳",
    "记账",
    "record_entry",
    "query_ledger",
    "已記入",
    "ledger",
)
_HISTORY_WINDOW = 800  # 只看最近這麼多字：很久以前談過帳本不能讓現在的追問被縮池


@dataclass(frozen=True)
class DomainDecision:
    """題型決策。``domain`` 為 None ＝ 不確定 ＝ 照現況（全工具、完整 prompt）。"""

    domain: Optional[str]
    reason: str

    @property
    def matched(self) -> bool:
        return self.domain is not None


def _normalize(text: str) -> str:
    return (
        re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().lower()
    )


def decide_domain(query: str, history: str = "") -> DomainDecision:
    """純規則、無 I/O。回傳題型或「不確定」。"""
    if not domain_gate_enabled():
        return DomainDecision(None, "disabled")
    q = _normalize(query)
    if not q:
        return DomainDecision(None, "empty")

    # 長訊息多半是多件事（貼文章、混著問），不縮
    if len(q) > 120:
        return DomainDecision(None, "too_long")

    if any(term in q for term in _CONFLICT_TERMS):
        return DomainDecision(None, "conflict")

    if any(term in q for term in _LEDGER_EXPLICIT):
        return DomainDecision(DOMAIN_LEDGER, "ledger_terms")

    if any(term in q for term in _LEDGER_GENERIC) and any(
        marker in f" {q} " for marker in _PERSONAL_MARKERS
    ):
        return DomainDecision(DOMAIN_LEDGER, "personal_finance_terms")

    if _QUICK_RECORD_RE.match(q):
        return DomainDecision(DOMAIN_LEDGER, "quick_record")

    if _FOLLOW_UP_RE.match(q):
        recent = _normalize((history or "")[-_HISTORY_WINDOW:])
        if any(term in recent for term in _HISTORY_LEDGER_TERMS):
            return DomainDecision(DOMAIN_LEDGER, "ledger_follow_up")
        return DomainDecision(None, "follow_up_without_ledger_context")

    return DomainDecision(None, "no_signal")


def tool_names_for(domain: Optional[str]) -> Optional[list]:
    """題型 → 允許的工具名；None ＝ 不縮。"""
    if domain == DOMAIN_LEDGER:
        return sorted(set(LEDGER_TOOLS) | set(CORE_TOOLS))
    return None
