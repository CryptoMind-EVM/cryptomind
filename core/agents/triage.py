"""T0 確定性請求分流（Triage Gate）。

問題背景
--------
`_claw_loop_node` 無條件把每個 query 丟進 cryptomind 的完整 ReAct loop——
連「你好」也會帶著全套 skill catalog + 幾十個 tool schema 打一次 LLM。
線上實測：一句寒暄從送出到第一個 token 花 47 秒，總計 79.5 秒，期間一個工具
都沒呼叫，純粹是巨大 prompt 的延遲。

設計原則
--------
1. **零 LLM、零 I/O**。用 LLM 判斷「這是不是簡單問題」本身就要好幾秒，會抵銷
   掉整個優化的意義。這裡只做純字串規則。
2. **寧可漏判，不可誤判**。漏判 = 照原本的慢路徑跑（現狀，沒有變差）；
   誤判 = 金融問題被小模型當寒暄回掉（嚴重）。所以先跑排除規則，
   任何一點金融訊號就直接放行到完整路徑。
3. **只認明確的寒暄**。白名單是完整比對而非子字串包含，避免
   「你好，幫我分析 BTC」這種開頭寒暄但實質是分析請求的句子被攔下。

命中後由呼叫端改用 `task_type="simple_qa"`（ModelRouter → gemini-3.5-flash）
直接回答，跳過工具裝載與 ReAct loop。
"""

from __future__ import annotations

import re
import unicodedata

# ── 排除規則：任何一條命中就「不」走快速通道 ──────────────────────────────
# 這裡刻意寬鬆（寧可多攔），因為誤判的代價遠高於漏判。

# 任何數字（價格、代號、年份、百分比…）都代表可能是資料查詢
_HAS_DIGIT_RE = re.compile(r"\d")

# 貨幣與百分比符號
_HAS_CURRENCY_RE = re.compile(r"[$€£¥₽%＄％]")

# 疑似股票/幣種代號：連續 2-6 個大寫英文字母。只給 has_finance_signal 用
# （T1 閘門），is_smalltalk 不套——理由見兩者的 docstring。
_TICKER_RE = re.compile(r"\b[A-Z]{2,6}\b")

# 金融/市場語彙。命中即代表使用者在問正事，不是寒暄。
#
# 注意這份表是「通用語彙」，不含公司名（台積電 / Tesla / …）——那是列不完的。
# 這不影響安全性：走快速通道的**充分條件**是通過下面 _SMALLTALK 的完整比對，
# 公司名不可能出現在那份白名單裡。這份表只是白名單之外的第二道防線。
# 涵蓋 zh-TW / zh-CN / en / ru（對齊 web/js/i18n 支援的語系）。
_FINANCE_TERMS = [
    # ── 中文（繁簡通用字 + 各自的異體）──
    "股", "幣", "币", "匯", "汇", "價", "价", "漲", "涨", "跌", "買", "买",
    "賣", "卖", "投資", "投资", "理財", "理财", "行情", "走勢", "走势",
    "分析", "報酬", "报酬", "收益", "獲利", "获利", "虧", "亏", "倉", "仓",
    "槓桿", "杠杆", "合約", "合约", "現貨", "现货", "期貨", "期货",
    "財報", "财报", "營收", "营收", "配息", "殖利率", "市值", "成交量",
    "多少錢", "多少钱", "值不值", "該不該", "该不该", "適合", "适合",
    "推薦", "推荐", "建議", "建议", "預測", "预测", "風險", "风险",
    "錢包", "钱包", "轉帳", "转账", "地址", "空投", "質押", "质押",
    "黃金", "黄金", "白銀", "白银", "原油", "美元", "台幣", "台币",
    "人民幣", "人民币", "日圓", "日元", "歐元", "欧元",
    # 帳本／個人收支紀錄：要靠 query_ledger／record_entry 等工具才答得出來，快速通道（無工具）
    # 只會回「我無法查看你的帳本」（2026-10-05 線上：「記錄了嗎」「幫我查看帳本」被當閒聊回掉）
    "帳本", "帐本", "账本", "記帳", "记账", "記錄", "记录", "紀錄", "纪录", "記一筆", "记一笔",
    "收支", "支出", "收入", "花費", "花费", "開銷", "开销", "欠", "借款", "還款", "还款",
    "存款", "資產", "资产", "持倉", "持仓",
    # ── 英文 ──
    "stock", "crypto", "coin", "token", "price", "market", "trade",
    "trading", "buy", "sell", "invest", "portfolio", "profit", "loss",
    "bull", "bear", "chart", "analysis", "analyse", "analyze", "forecast",
    "dividend", "earnings", "revenue", "wallet", "address", "airdrop",
    "staking", "leverage", "futures", "options", "etf", "nft", "defi",
    "gold", "silver", "oil", "forex", "usd", "eur", "twd",
    "should i", "worth", "recommend", "predict", "risk",
    "ledger", "journal", "expense", "income", "spent", "owe", "debt", "recorded",
    "bookkeeping", "my records", "my balance",
    # ── 俄文 ──
    "акци", "крипт", "монет", "токен", "цен", "рынок", "торг",
    "куп", "прода", "инвест", "прибыл", "убыт", "анализ", "прогноз",
    "кошел", "адрес", "золот", "нефт", "доллар", "евро", "риск",
    "журнал", "расход", "доход", "долг", "записа", "учёт", "учет",
]

# ── 「要動用 agent 功能」的語彙（2026-10-06 審查）─────────────────────────────
# 記憶、技能、行事曆提醒、判斷紀錄都靠工具（remember／list_my_skills_memory／propose_custom_skill／
# add_calendar_event／record_call）與長期記憶才答得了。T1 的分類只有 simple_qa／deep_analysis 兩類，
# 「你還記得我的偏好嗎」「幫我新增一個技能」「明天提醒我看財報」會被當成「問助手自己」而走無工具、
# 無記憶的快速通道——回出「我沒有記憶」這種與事實相反的答案，或承諾做了卻沒做。
# 這些詞一律放行到完整路徑（跟金融語彙同一個「寧可漏判」原則）。
_AGENT_FEATURE_TERMS = [
    # 記憶
    "記憶", "记忆", "記得", "记得", "記住", "记住", "記下", "记下", "忘記", "忘记", "忘掉", "偏好",
    "之前說", "之前说", "我說過", "我说过", "你知道我", "認識我", "认识我", "我是誰", "我是谁",
    "remember", "memory", "forget", "do you know me", "my preference",
    "помни", "запомн", "памят", "забудь",
    # 技能
    "技能", "自訂", "自定義", "自定义", "新增技能", "skill",
    "навык",
    # 提醒／行事曆
    "提醒", "行事曆", "行事历", "日曆", "日历", "排程", "calendar", "remind",
    "напомн", "календар",
    # 判斷紀錄（record_call）
    "判斷", "判断", "看多", "看空", "喊單", "喊单",
]  # fmt: skip

# ── 白名單：明確的寒暄 / 閒聊 / 系統性問候 ────────────────────────────────
# 完整比對（normalize 後），不做子字串包含。
_SMALLTALK = {
    # 中文問候
    "你好", "妳好", "您好", "哈囉", "哈啰", "哈罗", "嗨",
    "早安", "午安", "晚安", "早上好", "中午好", "晚上好", "早", "在嗎", "在吗",
    "你好啊", "你好呀", "您好啊",
    # 中文致謝 / 收尾
    "謝謝", "谢谢", "感謝", "感谢", "多謝", "多谢", "謝了", "谢了",
    "好的", "好", "了解", "知道了", "收到", "沒事", "没事", "不用了",
    "再見", "再见", "掰掰", "拜拜", "byebye",
    # 中文身分詢問（可用固定人設回答，不需工具）
    "你是誰", "你是谁", "你是什麼", "你是什么", "你叫什麼", "你叫什么",
    "你會什麼", "你会什么", "你能做什麼", "你能做什么", "你可以做什麼",
    "你可以做什么", "有什麼功能", "有什么功能", "怎麼用", "怎么用",
    "如何使用", "使用說明", "使用说明", "幫助", "帮助",
    # 英文
    "hi", "hello", "hey", "yo", "hiya", "howdy",
    "good morning", "good afternoon", "good evening", "good night",
    "thanks", "thank you", "thx", "ty", "cheers",
    "ok", "okay", "got it", "understood", "nice", "cool", "great",
    "bye", "goodbye", "see you", "see ya",
    "who are you", "what are you", "what can you do", "what do you do",
    "how do i use this", "how to use", "help", "what is this",
    # 俄文
    "привет", "хай", "здравствуйте", "здравствуй", "добрый день", "доброе утро",
    "добрый вечер", "спасибо", "благодарю", "пока", "до свидания",
    "хорошо", "понятно", "ясно", "кто ты", "что ты умеешь", "помощь",
}

# normalize 時要剝掉的結尾標點（含全形）
_TRAILING_PUNCT = "!?.,~！？。，、；;：: \t\n\r…"

# 長度上限（normalize 後）。超過就不可能是單純寒暄。
_MAX_LEN = 24


def _normalize(text: str) -> str:
    """小寫化 + 全形轉半形 + 去除空白與結尾標點。"""
    # NFKC 把全形英數/標點轉半形，讓「ＨＥＬＬＯ」與「hello」等價
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.strip().lower()
    # 去掉頭尾標點（含重複的「你好！！！」）
    normalized = normalized.strip(_TRAILING_PUNCT)
    # 中文句中的空白對比對沒意義，但英文片語需要保留單一空格
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def normalize_query(text: str) -> str:
    """公開版的 query 正規化（小寫、全形轉半形、去頭尾標點與多餘空白）。

    給需要「同一個問題的穩定 key」的呼叫端用，例如 response_cache。
    """
    return _normalize(text)


def is_smalltalk(text: str) -> bool:
    """是否為可以走 T0 快速通道的寒暄 / 閒聊 / 系統性問候。

    純規則、無 I/O。任何不確定的情況一律回 False（退回完整 ReAct 路徑）。

    安全性的根基是最後那行**完整比對**白名單——不是子字串包含，所以
    「你好，幫我分析 BTC」不會命中。前面的長度／數字／金融語彙檢查是第二道
    防線，防的是白名單日後被加進不安全的項目。

    刻意不加「疑似代號」的 ticker regex（``[A-Z]{2,6}``）：它會把使用者打的
    "OK" / "HELLO" 這類全大寫寒暄誤判成股票代號，而完整比對白名單已經涵蓋
    了它想防的情況。
    """
    if not text:
        return False

    normalized = _normalize(text)
    if not normalized or len(normalized) > _MAX_LEN:
        return False

    # 排除優先：數字 / 貨幣符號 / 金融語彙一律放行到完整路徑。
    # 這裡刻意不套 ticker regex，理由見 docstring。
    if _has_vocabulary_signal(normalized):
        return False

    return normalized in _SMALLTALK


def _has_vocabulary_signal(normalized: str) -> bool:
    """數字 / 貨幣符號 / 通用金融語彙（不含 ticker 判斷）。

    拆出來是因為兩個呼叫端對 ticker 的需求相反：
    ``is_smalltalk`` 不能用（會誤殺 "OK" / "HELLO"），
    ``has_finance_signal`` 必須用（否則「BTC 多少」漏判）。
    """
    if not normalized:
        return False
    if _HAS_DIGIT_RE.search(normalized) or _HAS_CURRENCY_RE.search(normalized):
        return True
    return any(term in normalized for term in _FINANCE_TERMS) or any(
        term in normalized for term in _AGENT_FEATURE_TERMS
    )


def has_finance_signal(text: str) -> bool:
    """是否含任何金融/市場訊號（數字、貨幣符號、金融語彙、疑似代號）。

    用途是當 T1 分類器的**前置閘門**：有金融訊號的 query 直接走完整 ReAct，
    連分類都不用跑（省掉那一次 LLM 呼叫的延遲）。

    含 ticker regex（連續 2-6 個大寫英文字母），所以「BTC 多少」這種
    語彙表抓不到的問法也會被擋下。全大寫寒暄（"OK" / "HELLO"）會被它誤判成
    代號，但那不影響結果——那些字串在更早的 T0 白名單就命中了，根本走不到
    這個閘門。

    仍然刻意不含公司名（台積電 / Tesla / …）——那是列不完的。所以它是
    「有訊號一定是金融問題」而非「沒訊號一定不是」，只能拿來當**放行**判斷，
    不能拿來當「安全可以走快速通道」的唯一依據。
    """
    if not text:
        return False
    if _TICKER_RE.search(text):
        return True
    return _has_vocabulary_signal(_normalize(text))


# ── T1 輕量分類（單次小模型呼叫）──────────────────────────────────────────
# T0 的白名單只認明確寒暄，抓不到「你可以幫我做什麼」「這個平台安全嗎」
# 「我要怎麼開始」這類同樣不需要工具、卻沒有固定句式的問題。T1 補這一段。
#
# 成本控制：只有在 has_finance_signal() 為 False 時才呼叫。有金融訊號的 query
# 直接走完整 ReAct，不付這 ~300ms——所以真正的市場問題延遲完全不變。

SIMPLE_QA = "simple_qa"
DEEP_ANALYSIS = "deep_analysis"

_CLASSIFY_PROMPT = """Classify the user message into exactly one category.

simple_qa — small talk, or a question about the assistant itself: what it is, \
what it can do, how to use it, whether it is safe, how to get started. \
Answerable from the assistant's own description with NO data lookup.

deep_analysis — anything else, including any question that needs market data, \
prices, news, company or asset information, or any kind of research.

Answer with exactly one word: simple_qa or deep_analysis.
When unsure, answer deep_analysis.

User message: {query}"""


async def classify_query(query: str, llm_invoke) -> str:
    """T1 分類：回傳 ``SIMPLE_QA`` 或 ``DEEP_ANALYSIS``。

    ``llm_invoke`` 是一個 ``async (prompt: str) -> str`` 的可呼叫物件，由呼叫端
    注入（讓這個模組不依賴 manager，可單獨測試）。

    安全預設：金融訊號硬否決、模型回傳無法辨識、呼叫失敗——全都回
    ``DEEP_ANALYSIS``。誤判成 simple_qa 會讓市場問題被無工具的小模型回掉，
    代價遠高於多跑一次完整流程。
    """
    if not query or not query.strip():
        return DEEP_ANALYSIS

    # 硬否決：有金融訊號就不可能是 simple_qa，模型說什麼都不算
    if has_finance_signal(query):
        return DEEP_ANALYSIS

    try:
        raw = await llm_invoke(_CLASSIFY_PROMPT.format(query=query))
    except Exception:
        return DEEP_ANALYSIS

    answer = _normalize(str(raw or ""))
    # 只認完全等於 simple_qa 的回覆；模型多話（"Category: simple_qa"）也接受，
    # 但必須不含 deep_analysis 以免把「不是 simple_qa」讀反。
    if SIMPLE_QA in answer and DEEP_ANALYSIS not in answer:
        return SIMPLE_QA
    return DEEP_ANALYSIS
