"""
ReAct recovery helpers — 處理 LLM 各種「沒正常完成任務」的情境。

學 Hermes-Agent 的多層 recovery（conversation_loop.py:5090-5400），但大幅簡化：
只挑 CryptoMind 場景最常見的三種失敗模式：

1. **ack without tool**：LLM 回「好的我來查」之類的 ack，但**沒實際呼叫 tool**
   → 視為未完成，nudge 重跑一輪
2. **thinking-only response**：reasoning model（nemotron-3-super、Claude thinking
   等）思考完但 ``content`` 為空，reasoning 在 ``additional_kwargs.reasoning_content``
   → push prefill nudge，讓模型看到自己的思考後給出最終答案
3. **truly empty**：連 reasoning 都沒有 → 直接重打一次（nudge）

設計原則
--------
- **保守**：每種 recovery 最多 1 次（Hermes 是 2-3 次；我們先保守避免無限迴圈）
- **總量上限**：所有 recovery 加起來最多 ``TOTAL_GRACE_BUDGET`` 次
- **獨立計數**：每輪 ``execute_streaming`` 內 local dict 追蹤，不持久化
- **階層觸發**：先檢查 ack → 再 thinking-only → 最後 truly empty（避免誤判）
- **多語**：ack 關鍵詞覆蓋 zh-TW/zh-CN/en/ru
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Retry budget（保守）
#
# - 每類 recovery 上限：避免單一類別無限 retry
# - TOTAL_GRACE_BUDGET：所有 recovery 加總上限，學 Hermes 的 iteration budget
#   概念。例如 ack(1) + thinking(1) + empty(1) = 3，TOTAL 設 3 剛好夠用，
#   設 4 留一格 grace call。grace call 的意義是「即使預算用完，仍給模型最後
#   一次機會收尾」（Hermes 風格），避免硬切斷讓使用者看到半成品。
# ============================================================================
RETRY_BUDGET = {
    "ack_without_tool": 1,
    "thinking_only": 1,
    "truly_empty": 1,
}

# 所有 recovery 種類加起來的總上限。
# 預設 4：ack(1) + thinking(1) + empty(1) + 1 grace = 4。
# 若想更保守可降到 3（無 grace）；更寬鬆可升到 5。
TOTAL_GRACE_BUDGET = 4


# ============================================================================
# 1. Ack continuation 偵測
# ============================================================================

# 多語 ack 關鍵詞。case-insensitive 比對。
# 只認「將來式／進行式」的承諾（我來查、稍等、let me check）；「查一下」「сейчас」這類
# 在建議句、陳述句裡也很常見的詞不單獨算。再配合 ``_has_substance`` 排除已經有內容的短答案。
_ACK_PATTERNS = [
    # zh-TW / zh-CN
    r"我來",
    r"讓我",
    r"让我",
    r"馬上",
    r"马上",
    r"這就",
    r"这就",
    r"幫你查",
    r"帮你查",
    r"稍等",
    r"马上查",
    r"馬上查",
    r"我幫",
    r"我帮",
    r"為您",
    r"为您",
    r"請稍",
    r"请稍",
    r"正在查",
    r"我(?:先|再|去)?(?:查|看)(?:一下|看)",
    r"幫您",
    r"帮您",
    # en
    r"\blet me\b(?! know)",
    r"\bI'll check\b",
    r"\bI will (?:check|look|search|find)\b",
    r"\bchecking\b",
    r"\bjust a (?:moment|sec|second)\b",
    r"\bhold on\b",
    r"\bone moment\b",
    r"\bI'll (?:look|search|find)\b",
    r"\bI'm (?:checking|looking|searching)\b",
    # ru
    r"сейчас (?:проверю|посмотрю|найду|узнаю|поищу|уточню|гляну)",
    r"минуту",
    r"секунду",
    r"проверяю",
    r"ищу",
]
_ACK_RE = re.compile("|".join(_ACK_PATTERNS), re.IGNORECASE)

# ack 偵測的長度上限：超過就不當 ack（避免把長回應誤判）
_ACK_MAX_LENGTH = 80

# 已經有實質內容的跡象：有這些的短回覆是答案，不是「說要查但沒查」。
# 「我幫你算過了：月付約 2.3 萬，總利息約 85 萬。」「為您整理：50/30/20 法則…」
# 以前因為含「我幫」「為您」被判 ack，整句已串流給使用者後又注入 nudge 重跑。
# 1. 帶單位的數字結論（金額、百分比、萬／億、小數）。股票代號（2330）、期間（3 個月）不算
_DATA_NUMBER_RE = re.compile(
    r"[$¥€£＄￥]\s*\d"
    r"|\d\s*(?:%|％|萬|万|億|亿|兆|元|塊|块|倍|美元|美金|台幣|新台幣|人民幣|USDT?|TWD|NT\b)"
    r"|\d[.,]\d",
    re.IGNORECASE,
)
# 2. 冒號後面接了實際內容（冒號前沒有「查／看／找／搜」這類查詢動詞時才算；
#    「我來查：BTC 價格」的冒號後面只是查詢主題）
_COLON_RE = re.compile(r"[:：]")
_COLON_CONTENT_MIN_CHARS = 6
_LOOKUP_VERB_RE = re.compile(
    r"查|看|找|搜|check|look|search|find|провер|ищ|поищ|посмотр", re.IGNORECASE
)
# 3. 條列／標題／表格
_STRUCTURE_RE = re.compile(r"(?m)^\s*(?:[-*•·]\s|\d+[.)、]\s*\S|#{1,6}\s|\|)")


def _has_substance(reply: str) -> bool:
    """reply 是否已經帶有實質內容（數字結論、冒號後的內容、條列）。"""
    if _DATA_NUMBER_RE.search(reply) or _STRUCTURE_RE.search(reply):
        return True
    colon = _COLON_RE.search(reply)
    if colon and not _LOOKUP_VERB_RE.search(reply[: colon.start()]):
        return len("".join(reply[colon.end() :].split())) >= _COLON_CONTENT_MIN_CHARS
    return False


def looks_like_ack_without_tool(
    reply: str, used_tools: list, language: Optional[str] = None
) -> bool:
    """偵測「LLM 回 ack 但沒實際呼叫 tool」的失敗模式。

    四條件 AND（全滿足才觸發）：
    1. ``used_tools`` 是空的
    2. ``reply`` 長度 < ``_ACK_MAX_LENGTH``
    3. ``reply`` 含任一 ack 關鍵詞（多語，限將來式／進行式）
    4. ``reply`` 還沒有實質內容（數字結論、冒號後的內容、條列），見 ``_has_substance``

    Args:
        reply: LLM 的最終 reply（content 字串）。
        used_tools: 本次執行實際呼叫的工具名稱清單。
        language: 語言代碼（目前未使用於判斷，保留介面供未來細化）。

    Returns:
        True 若判定為 ack-without-tool 失敗模式。

    Examples:
        >>> looks_like_ack_without_tool("好的，我來查比特幣現價", [])
        True
        >>> looks_like_ack_without_tool("BTC 現價 $64,000，建議...", [])
        False
        >>> looks_like_ack_without_tool("好的，我來查", ["get_crypto_price"])
        False
        >>> looks_like_ack_without_tool("我幫你算過了：月付約 2.3 萬。", [])
        False
    """
    if used_tools:
        return False
    if not reply or not isinstance(reply, str):
        return False
    if len(reply) >= _ACK_MAX_LENGTH:
        return False
    return bool(_ACK_RE.search(reply)) and not _has_substance(reply)


# ============================================================================
# 2. Thinking-only response 偵測
# ============================================================================


def extract_reasoning_content(msg: Any) -> str:
    """從 LangChain Message 抓 reasoning_content（reasoning model 的思考內容）。

    不同 provider 放的位置略不同：
    - OpenAI reasoning model: ``response_metadata.reasoning_content`` 或
      ``additional_kwargs.reasoning_content``
    - NVIDIA nemotron: ``additional_kwargs.reasoning_content``
    - DeepSeek-R1 系 / 部分 Qwen3（OpenAI 相容端點）:
      ``additional_kwargs.reasoning``
    - 部分 OpenAI 相容 reasoning 模型: ``additional_kwargs.thinking`` /
      ``response_metadata.thinking``
    - Anthropic thinking: ``content`` 裡的 ``thinking`` type block

    我們檢查所有可能位置，回傳非空字串或空字串。
    """
    if msg is None:
        return ""

    # additional_kwargs — reasoning model 常見位置（涵蓋多種 key 命名慣例）
    ak = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(ak, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            rc = ak.get(key)
            if isinstance(rc, str) and rc.strip():
                return rc

    # response_metadata — 部分 provider 放這裡
    rm = getattr(msg, "response_metadata", None) or {}
    if isinstance(rm, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            rc = rm.get(key)
            if isinstance(rc, str) and rc.strip():
                return rc

    # Anthropic thinking block: content 是 list of {type: "thinking", thinking: "..."}
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "thinking":
                thinking = item.get("thinking") or item.get("content") or ""
                if isinstance(thinking, str) and thinking.strip():
                    parts.append(thinking)
        if parts:
            return "\n".join(parts)

    return ""


def is_thinking_only(msg: Any) -> bool:
    """偵測 reasoning model 的「思考完但答案空」失敗模式。

    條件：visible content 為空，但有 reasoning_content。

    Args:
        msg: LangChain Message（最後一條 AIMessage）。

    Returns:
        True 若判定為 thinking-only。
    """
    if msg is None:
        return False

    # visible content
    content = getattr(msg, "content", None)
    visible_text = ""
    if isinstance(content, str):
        visible_text = content.strip()
    elif isinstance(content, list):
        visible_text = "".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") in (None, "text")
        ).strip()

    if visible_text:
        return False  # 有 visible content → 不是 thinking-only

    # 有 reasoning 才算
    return bool(extract_reasoning_content(msg))


# ============================================================================
# 3. Retry budget tracking（含 grace call 概念）
# ============================================================================


class RetryCounter:
    """追蹤各類 recovery 的剩餘次數 + 總量上限。

    一個 ``execute_streaming`` 一個 instance。用 dict 存剩餘次數，
    每次消耗一格。用 ``can_retry(kind)`` 檢查、``consume(kind)`` 消耗。

    總量上限 ``TOTAL_GRACE_BUDGET`` 學 Hermes 的 iteration budget：
    即使某類別額度還有，只要總消耗超過上限就不允許再 retry。
    最後一格視為 grace call（讓模型有機會收尾）。
    """

    def __init__(self, total_budget: int = TOTAL_GRACE_BUDGET) -> None:
        self._remaining: dict[str, int] = dict(RETRY_BUDGET)
        self._total_budget = total_budget
        self._total_consumed = 0

    def can_retry(self, kind: str) -> bool:
        """檢查某類 recovery 是否還有額度（且總量未超）。"""
        if self._remaining.get(kind, 0) <= 0:
            return False
        if self._total_consumed >= self._total_budget:
            return False
        return True

    def consume(self, kind: str) -> None:
        """消耗一格（不檢查邊界，呼叫端應先 ``can_retry``）。"""
        current = self._remaining.get(kind, 0)
        self._remaining[kind] = max(0, current - 1)
        self._total_consumed += 1

    def remaining(self, kind: str) -> int:
        return self._remaining.get(kind, 0)

    def total_remaining(self) -> int:
        """總剩餘（取類別剩餘總和 vs 總量上限的較小者）。"""
        by_kind = sum(self._remaining.values())
        by_total = max(0, self._total_budget - self._total_consumed)
        return min(by_kind, by_total)

    def total_consumed(self) -> int:
        return self._total_consumed

    def is_grace_call(self) -> bool:
        """是否正在使用最後一格（grace call）。

        Hermes 風格：最後一格是 grace，呼叫端可藉此決定要不要給更寬鬆的 nudge。
        """
        return self._total_consumed == self._total_budget - 1

    def __repr__(self) -> str:
        return (
            f"RetryCounter(remaining={self._remaining}, "
            f"total={self._total_consumed}/{self._total_budget})"
        )


__all__ = [
    "RETRY_BUDGET",
    "TOTAL_GRACE_BUDGET",
    "looks_like_ack_without_tool",
    "extract_reasoning_content",
    "is_thinking_only",
    "RetryCounter",
]
