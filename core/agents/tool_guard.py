"""Per-turn 工具呼叫防護 — 去重 + 輪數上限。

業界共識（見 web_search 研究筆記）reasoning agent 必須有兩道防護，否則會陷入
「過度思考 → 反覆呼叫相同/相似工具」的循環，吃光 timeout：

1. **去重（debounce / 指紋）**：偵測完全相同或關鍵詞高度重疊的重複呼叫，第二次
   直接回快取或攔截。線上 #特斯拉案例：LLM 連續兩輪搜尋「Tesla Q2 2026 earnings
   revenue」幾乎相同的 query，每輪又觸發 Jina 抓 16000 字，跑滿 300s 超時。
   來源：Dev.to「Prevent Reasoning Loops」實測一個模糊工具導致 14 次重複呼叫。

2. **輪數上限（hard limit）**：慢工具（web_search / fetch_url）每 turn 設上限，
   超過強制 LLM 用現有資料回答。來源：n8n feature request、DeepSeek agent 指南
   「give each tool its own timeout / keep a small budget」。

設計：
- 用 thread-local 存 per-turn 狀態（同 claw_loop 的 fallback_guard 模式）。
- claw_loop 每個 turn 開頭呼叫 ``reset_turn_tool_guard()`` 重設。
- 工具 wrapper（tools.py）呼叫前呼叫 ``check_tool_guard(tool_name, **kwargs)``，
   回傳 ``(should_block, message)``：should_block=True 時工具不執行、直接回 message
   給 LLM（告訴它「已搜過類似 query / 已達輪數上限，請用現有資料回答」）。

只依賴 typing + threading，不引用任何 agent 內部類別，避免循環依賴。
"""

from __future__ import annotations

import re
import threading
from typing import Any, Tuple

from api.utils import logger

#: 慢工具的每 turn 呼叫上限。業界對 web search 這類慢工具建議 2-3 次；設 3
#: 給 reasoning 模型一點空間（第一輪可能不精準），但擋住第 4 次起的失控。
#: 來源：Dev.to flight search 實測設 2；n8n「max tool interactions」討論。
_TOOL_TURN_LIMITS: dict[str, int] = {
    "web_search": 3,
    "fetch_url": 4,
}

#: 去重視窗：相同工具 + 指紋的呼叫在 N 次內視為重複（滑動窗口）。
#: 不用時間（agent 可能跨秒累積），用「最近 N 次」更可預測。
_DEDUP_WINDOW = 5

#: 相似度門檻：Jaccard（關鍵詞重疊率）≥ 此值視為「相似 query」。
#: 0.45 — 線上特斯拉案例兩個 query（"TSLA Q2 earnings revenue delivery" vs
#: "Q2 revenue EPS gross margin earnings report"）Jaccard≈0.39，純詞重疊不足以
#: 偵測「語義相同但用詞分散」。設 0.45 + 核心實體輔助判斷（見 _shares_core_entity）。
_SIMILARITY_THRESHOLD = 0.45


class _TurnState:
    """單一 turn 的工具呼叫狀態（thread-local）。"""

    def __init__(self) -> None:
        self.call_counts: dict[str, int] = {}
        # 每個工具的近期呼叫指紋清單（滑動窗口，最多 _DEDUP_WINDOW 個）
        self.recent_fingerprints: dict[str, list[str]] = {}


_tls = threading.local()


def _get_state() -> _TurnState:
    """取得當前 thread 的 turn 狀態；不存在則建一個（容錯，不拋）。"""
    state = getattr(_tls, "tool_guard", None)
    if state is None:
        state = _TurnState()
        _tls.tool_guard = state
    return state


def reset_turn_tool_guard() -> None:
    """每個 turn 開頭重設計數器與去重視窗。

    由 claw_loop._claw_loop_node 呼叫（同 reset_turn_fallback_guard 模式）。
    """
    state = _get_state()
    state.call_counts.clear()
    state.recent_fingerprints.clear()


def _tokenize(text: str) -> set[str]:
    """把 query 切成關鍵詞集合（小寫、去停用詞、去標點）。

    用於相似度計算（Jaccard）。簡單切詞即可——目的是偵測「同一件事換個說法
    再查一次」，不需要精確 NLP。

    中文用 bigram（相鄰二字元）切分——無詞表的標準做法。例：
    「比特幣價格分析」→ {比特, 特幣, 幣價, 價格, 格分, 分析}，
    「比特幣價格預測」→ {比特, 特幣, 幣價, 價格, 格預, 預測}，
    兩者重疊 {比特,特幣,幣價,價格} → Jaccard 高，正確偵測為相似。
    英文按空白/標點切單詞。
    """
    if not text:
        return set()
    lowered = text.lower()
    tokens: set[str] = set()
    # 英文/數字單詞
    tokens.update(re.findall(r"[a-z0-9]+", lowered))
    # 中文 bigram（連續中文字元的相鄰二字元組）
    for cn_run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        for i in range(len(cn_run) - 1):
            tokens.add(cn_run[i : i + 2])
        # 單字中文（len==1）也保留
        if len(cn_run) == 1:
            tokens.add(cn_run)
    # 停用詞（英文常見 + 通用）
    stop = {
        "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at",
        "is", "are", "was", "were", "be", "with", "from", "by", "this", "that",
        "news", "latest", "update", "2024", "2025", "2026",  # 年份不具區分力
    }
    return tokens - stop


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度：|A∩B| / |A∪B|。兩空集合回 0（不誤判為相同）。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


#: 主題關鍵詞 — 財經查詢的「問什麼」。兩個 query 共享標的 + 至少一個主題詞
#: → 語義上在查同一件事（即使 Jaccard 詞重疊低）。
#: 線上特斯拉案例：兩個 query 都含 tesla + earnings/revenue，但用詞分散
#: （results/report、delivery/eps），純 Jaccard 抓不到，靠這個輔助。
_TOPIC_KEYWORDS: set[str] = {
    "earnings", "revenue", "price", "forecast", "analysis", "outlook",
    "news", "report", "results", "financial", "quarter", "q1", "q2",
    "q3", "q4", "guidance", "margin", "profit", "loss", "delivery",
    "production", "sales", "growth", "decline", "risk", "competition",
    "hack", "vulnerability", "launch", "upgrade", "downgrade", "target",
    "estimates", "eps", "pe", "valuation", "bull", "bear", "etf",
    # 中文主題詞（bigram 形式，對齊 _tokenize 的中文切分）
    "財報", "營收", "價格", "分析", "預測", "新聞", "獲利", "虧損",
    "成長", "衰退", "風險", "競爭", "目標", "估值", "展望", "營利",
}


def _extract_entities(tokens: set[str]) -> Tuple[set[str], set[str]]:
    """從 token 集合抽出 (標的實體, 主題詞)。

    標的實體 = 非主題詞、非停用詞的內容詞（公司名/ticker/幣種/數字代號）。
    主題詞 = 命中 _TOPIC_KEYWORDS 的詞。
    """
    topics = tokens & _TOPIC_KEYWORDS
    # 標的 = 剩下的（排除純年份、停用詞已在 _tokenize 處理）
    entities = tokens - _TOPIC_KEYWORDS
    return entities, topics


def _shares_core_entity(a: set[str], b: set[str]) -> bool:
    """兩個 query 是否共享「標的實體 + 至少一個主題詞」。

    補 Jaccard 的不足：語義相同但用詞分散的查詢（如特斯拉財報換個角度問）。
    條件：共享 ≥1 個標的實體 AND 共享 ≥1 個主題詞。
    例：{tesla, tsla, earnings, revenue} vs {tesla, revenue, eps, margin}
        → 共享 tesla（標的）+ revenue（主題）→ True。
    """
    a_entities, a_topics = _extract_entities(a)
    b_entities, b_topics = _extract_entities(b)
    # 至少共享一個標的實體
    if not (a_entities & b_entities):
        return False
    # 至少共享一個主題詞
    if not (a_topics & b_topics):
        return False
    return True


def _make_fingerprint(tool_name: str, **kwargs: Any) -> Tuple[str, set[str]]:
    """從工具參數生成指紋字串（去重用）+ 關鍵詞集合（相似度用）。

    回傳 (exact_fingerprint, keyword_set)。exact_fingerprint 用於偵測完全相同
    的重複呼叫；keyword_set 用於偵測「相似但不完全相同」的呼叫。
    """
    # web_search / fetch_url 的主要參數是 query / url
    primary = ""
    for key in ("query", "url", "symbol", "keyword"):
        val = kwargs.get(key)
        if val:
            primary = str(val).strip().lower()
            break
    # 完全相同指紋：tool + 主要參數（normalize 空白）
    normalized = re.sub(r"\s+", " ", primary).strip()
    exact_fp = f"{tool_name}|{normalized}"
    # 關鍵詞集合（相似度）
    keywords = _tokenize(primary)
    return exact_fp, keywords


def check_tool_guard(tool_name: str, **kwargs: Any) -> Tuple[bool, str]:
    """檢查工具呼叫是否該被防護層攔截。

    Args:
        tool_name: 工具名（如 "web_search"、"fetch_url"）。
        **kwargs: 工具參數（會從 query/url/symbol 等抽指紋）。

    Returns:
        (should_block, message)：
        - should_block=True：工具不該執行，直接回 message 給 LLM。
        - should_block=False：正常執行，message 為空字串。

    攔截條件（先到先擋）：
        1. 達該工具的輪數上限 → 擋（告訴 LLM 用現有資料回答）
        2. 與近期呼叫完全相同（exact fingerprint 命中）→ 擋（回「已搜過」）
        3. 與近期呼叫高度相似（Jaccard ≥ 門檻）→ 擋（回「已搜過類似 query」）
    """
    state = _get_state()

    # 不在限制清單的工具 → 不攔截（放行專用工具如 get_crypto_price）
    limit = _TOOL_TURN_LIMITS.get(tool_name)
    if limit is None:
        return False, ""

    # 1. 輪數上限
    count = state.call_counts.get(tool_name, 0)
    if count >= limit:
        logger.info(
            "[ToolGuard] %s 已達 per-turn 上限 %d 次，攔截重複呼叫",
            tool_name,
            limit,
        )
        return True, (
            f"[{tool_name} 達本輪呼叫上限 {limit} 次] "
            f"你已經累積足夠的搜尋/資料結果。請直接用「現有」的工具結果回答使用者，"
            f"不要再次呼叫 {tool_name}。若現有資料不足，明確告知使用者哪些資訊無法取得。"
        )

    # 2 & 3. 去重（完全相同 + 相似）
    exact_fp, keywords = _make_fingerprint(tool_name, **kwargs)
    recent = state.recent_fingerprints.get(tool_name, [])

    # 完全相同
    if exact_fp in recent:
        logger.info("[ToolGuard] %s 偵測到完全相同的重複呼叫，攔截: %s", tool_name, exact_fp)
        return True, (
            f"[{tool_name} 重複呼叫] 你剛剛已經用完全相同的參數呼叫過 {tool_name}，"
            f"結果相同不需要再查。請用先前的結果回答，或換個不同的搜尋角度。"
        )

    # 相似（Jaccard 或 核心實體共享）— 僅對 query 類工具（web_search）。
    # fetch_url 的 URL 結構性共享 domain（example.com/a vs /b）會誤判為相似，
    # URL 只用完全相同去重（上面已處理）。
    if tool_name == "web_search" and keywords:
        for prev_fp in recent:
            # prev_fp 格式 "tool|query"，取 query 部分算相似度
            prev_query = prev_fp.split("|", 1)[1] if "|" in prev_fp else ""
            prev_keywords = _tokenize(prev_query)
            sim = _jaccard(keywords, prev_keywords)
            # 雙重判斷：純詞重疊（Jaccard）OR 語義重疊（標的+主題共享）
            shares_entity = _shares_core_entity(keywords, prev_keywords)
            is_similar = sim >= _SIMILARITY_THRESHOLD or shares_entity
            if is_similar:
                reason = (
                    f"關鍵詞 {int(sim * 100)}% 相同"
                    if sim >= _SIMILARITY_THRESHOLD
                    else "查詢同一標的的同類資訊"
                )
                logger.info(
                    "[ToolGuard] %s 偵測到相似呼叫（%s），攔截:\n  新: %s\n  舊: %s",
                    tool_name,
                    reason,
                    exact_fp.split("|", 1)[1] if "|" in exact_fp else "",
                    prev_query,
                )
                return True, (
                    f"[{tool_name} 重複呼叫] 這個搜尋與你剛剛查過的內容重疊（{reason}）。"
                    f"請用先前的搜尋結果回答，或換個「完全不同」的角度"
                    f"（例如改查財報數字而非新聞，或改查技術面而非基本面，或改查不同標的）。"
                )

    # 通過防護 → 記錄這次呼叫
    state.call_counts[tool_name] = count + 1
    recent.append(exact_fp)
    # 滑動窗口：只保留最近 _DEDUP_WINDOW 個
    if len(recent) > _DEDUP_WINDOW:
        recent.pop(0)
    state.recent_fingerprints[tool_name] = recent

    return False, ""
