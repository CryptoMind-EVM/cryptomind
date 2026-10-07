"""
Numeric Verification — 數字一致性檢查（學 outcome grading + post-hoc verification）。

防止「2.22% 跌幅被幻覺成 660 點盤中波動」這類幻覺：
response 中出現的金融關鍵數字（價格、點數、百分比），必須能在 tool 輸出中
找到對應來源。若找不到 → 標記為可疑幻覺。

設計原則
--------
- **保守**：只檢查「金融關鍵數字」（帶貨幣符號 / 帶 % / 帶「點」單位），
  不檢查一般數字（如「3 個建議」「Step 1」），避免假陽性
- **容許簡單推論**：若數字是 tool 輸出中其他數字的簡單四則運算結果（如
  13.50 - 13.20 = 0.30），不算幻覺
- **只觸發 1 次**：可疑時 nudge LLM 重跑一次，不無限迴圈
- **只對照市場資料**：驗證來源只算「市場資料類工具」的輸出（見 ``NON_MARKET_TOOLS``）；
  只用了 resolve_symbol／時間錨點這類工具的回答（個人理財試算等）無從驗證，直接放行
- **使用者自己的數字不是幻覺**：本輪問題與對話歷史出現過的數字（含「6 萬＝60000」
  這類換算）及其簡單運算（佔比、百分比分配、加減乘除）都算有來源
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterator, List

logger = logging.getLogger(__name__)


# ============================================================================
# 1. 從文字中抽取金融關鍵數字
# ============================================================================

# 金融關鍵數字模式：
# - 貨幣：$64,000 / $13.20 / NT$2,290 / ¥150
# - 百分比：+1.62% / -2.22% / 0.96%
# - 點數：660 點 / 660點 / 3000 points
# - 市值/TVL：1.28 兆 / 4.55 億 / 222B / 1.28T
_FINANCIAL_NUMBER_PATTERNS = [
    # 貨幣符號 + 數字（含千分位逗號）
    re.compile(r"(?:US\$|NT\$|HK\$|¥|€|£|\$)\s*[\d,]+\.?\d*", re.IGNORECASE),
    # 百分比
    re.compile(r"[+-]?[\d,]+\.?\d*\s*%"),
    # 「N 點」/ 「N points」（移除 \b，因為中文「點」後面接「的」「波動」等
    # 不會構成英文 word boundary，會導致「660 點的波動」匹配失敗）
    re.compile(r"[\d,]+\.?\d*\s*(?:點|points?)", re.IGNORECASE),
    # 市值單位
    re.compile(r"[\d,]+\.?\d*\s*(?:兆|億|萬|B\b|M\b|T\b|K\b)", re.IGNORECASE),
]

# 一般數字（不含金融 context）— 用來容許「Step 1」「3 個建議」等
_GENERIC_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass
class ExtractedNumber:
    """從文字中抽出的數字。"""

    raw: str  # 原始匹配字串（如 "$64,000" / "660 點" / "+1.62%"）
    value: float  # 純數值（如 64000.0 / 660.0 / 1.62）
    context: str = ""  # 前 20 字元 context（除錯用）


def extract_financial_numbers(text: str) -> List[ExtractedNumber]:
    """從文字中抽出金融關鍵數字。

    只抽「有金融 context 的」數字（貨幣、百分比、點數、市值），
    不抽一般數字（如「3 個建議」），降低假陽性。

    Args:
        text: LLM 回應或 tool 輸出文字。

    Returns:
        抽出的金融數字清單。
    """
    results: list[ExtractedNumber] = []
    if not text or not isinstance(text, str):
        return results

    seen_positions: set[int] = set()  # 避免重複匹配同一位置

    for pattern in _FINANCIAL_NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            # 避免跟前面 pattern 重疊
            if any(match.start() <= pos <= match.end() for pos in seen_positions):
                continue

            raw = match.group()
            # 從 raw 抽純數值
            num_str = re.sub(r"[^\d.+-]", "", raw.replace(",", ""))
            try:
                value = float(num_str)
            except ValueError:
                continue

            context_start = max(0, match.start() - 20)
            context = text[context_start : match.end()]

            results.append(ExtractedNumber(raw=raw, value=value, context=context))
            seen_positions.add(match.start())

    return results


# 數量級單位 → 倍率。回答與使用者都常寫「6 萬」「2.3 億」「3k」「1.28T」
_MAGNITUDE_SCALES = {
    "兆": 1e12,
    "億": 1e8,
    "亿": 1e8,
    "萬": 1e4,
    "万": 1e4,
    "千": 1e3,
    "百": 1e2,
    "t": 1e12,
    "b": 1e9,
    "m": 1e6,
    "k": 1e3,
}
_RAW_UNIT_RE = re.compile(r"(兆|億|亿|萬|万|[tbmk])\s*$", re.IGNORECASE)


def _unit_scale(raw: str) -> float:
    """``ExtractedNumber.raw`` 尾端的數量級單位倍率（無單位回 1.0）。"""
    m = _RAW_UNIT_RE.search(raw or "")
    return _MAGNITUDE_SCALES.get(m.group(1).lower(), 1.0) if m else 1.0


# 使用者文字裡的數字：阿拉伯數字（含千分位）＋可選的「百／千」「萬／億／兆」或 k/M/B/T。
# 英文單位後面不能再接字母（避免 "6 months" 的 m）。
_CONTEXT_NUMBER_RE = re.compile(
    r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(百|千)?\s*(萬|万|億|亿|兆)?(?:([kKmMbBtT])(?![A-Za-z]))?"
)
# 「月薪六萬」「十二萬」「三百萬」：中文數字只在後面接 萬／億 時才認，避免把「一下」「統一」當數字
#（前面不能是阿拉伯數字：「3 百萬」的「百萬」由上面的數字 regex 處理）
_CN_NUMERAL_RE = re.compile(
    r"(?<!\d)(?<!\d\s)([零〇一二兩两三四五六七八九十百千]+)\s*(萬|万|億|亿)"
)
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}  # fmt: skip
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
# 使用者數字最多留最近 N 個（含換算值）：歷史很長時不讓比對成本失控
_CONTEXT_NUMBER_LIMIT = 60


def _cn_numeral_to_int(text: str) -> int | None:
    """「六」「十二」「三百」「一百二十」→ int；看不懂（如「兩三」）回 None。"""
    if len(text) > 1 and not any(ch in _CN_UNITS for ch in text):
        return None
    total = cur = 0
    for ch in text:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        else:
            total += (cur or 1) * _CN_UNITS[ch]
            cur = 0
    total += cur
    return total or None


def extract_context_numbers(
    text: str, limit: int = _CONTEXT_NUMBER_LIMIT
) -> List[float]:
    """抽出使用者文字（本輪問題＋對話歷史）裡的數字，含數量級換算。

    「月薪 6 萬」→ [6, 60000]；「3k」→ [3, 3000]；「月薪六萬」→ [60000]。
    不看金融 context（使用者講的任何數字都算他自己的來源），去重後保留最近 ``limit`` 個。
    """
    if not text or not isinstance(text, str):
        return []
    found: list[float] = []

    def _add(value: float) -> None:
        if value not in found:
            found.append(value)

    for m in _CONTEXT_NUMBER_RE.finditer(text):
        try:
            base = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        _add(base)
        scale = 1.0
        for unit in (m.group(2), m.group(3), m.group(4)):
            if unit:
                scale *= _MAGNITUDE_SCALES[unit.lower()]
        if scale != 1.0:
            _add(base * scale)
    for m in _CN_NUMERAL_RE.finditer(text):
        n = _cn_numeral_to_int(m.group(1))
        if n is not None:
            _add(float(n * _MAGNITUDE_SCALES[m.group(2)]))
    return found[-limit:]


def extract_all_numbers(text: str) -> List[float]:
    """抽出所有數字（含一般數字），用來做「簡單推論」容許檢查。"""
    if not text:
        return []
    return [
        float(m.group())
        for m in _GENERIC_NUMBER_RE.finditer(text)
        if m.group().replace(".", "", 1).isdigit()
    ]


# ============================================================================
# 2. 數字一致性比對
# ============================================================================


def _is_simple_derivation(
    target: float, source_numbers: list[float], tolerance: float = 0.02
) -> bool:
    """檢查 target 是否為 source_numbers 的簡單四則運算結果。

    容許模式（tolerance 2%）：
    - 直接出現在 source（精確匹配）
    - A - B（如 13.50 - 13.20 = 0.30）
    - A + B
    - A / B（如 64000 / 100 = 640）
    - A * ratio（常見比例：1/2, 1/3, 1/4, 2x, 3x）
    - 百分比換算（A * B / 100）

    Args:
        target: 要驗證的數字。
        source_numbers: tool 輸出中的所有數字。
        tolerance: 容許誤差（預設 2%）。

    Returns:
        True 若 target 是 source 的簡單推論。
    """
    if not source_numbers:
        return False

    # 直接匹配（含容許誤差）
    for s in source_numbers:
        if abs(target - s) <= max(abs(s) * tolerance, 0.01):
            return True

    # 兩數運算
    for i, a in enumerate(source_numbers):
        for j, b in enumerate(source_numbers):
            if i == j:
                continue
            try:
                if abs(target - (a - b)) <= max(abs(target) * tolerance, 0.01):
                    return True
                if abs(target - (a + b)) <= max(abs(target) * tolerance, 0.01):
                    return True
                if b != 0 and abs(target - (a / b)) <= max(
                    abs(target) * tolerance, 0.01
                ):
                    return True
                if abs(target - (a * b)) <= max(abs(target) * tolerance, 0.01):
                    return True
            except (OverflowError, ValueError):
                continue

    # 百分換算（如 13.5 * 2.22 / 100 = 0.2997 ≈ 0.30）。
    # 只容許「target 本身 < 100」（百分比換算結果通常是小數，如 0.30），
    # 且其中一個 source 是百分比（0~100）。避免兩個大數字乘除湊出大目標。
    if target >= 100:
        pass  # target 太大，跳過百分換算
    else:
        for s in source_numbers:
            if s <= 0 or s > 100:
                continue  # s 必須是百分比（0 < s <= 100）
            for s2 in source_numbers:
                if s2 == 0:
                    continue
                pct_result = s * s2 / 100
                if abs(target - pct_result) <= max(abs(target) * tolerance, 0.01):
                    return True

    return False


# 使用者數字的簡單運算只容許很小的誤差：數字池是模型自己寫的，容許太寬會讓任何
# 捏造值都剛好「湊得出來」。四捨五入（16.67 → 17、16.7）另外處理，不靠放寬容許。
_CONTEXT_DERIVATION_TOLERANCE = 0.01


def _matches(target: float, value: float, tolerance: float) -> bool:
    """target 與 value 在容許誤差內，或 target 是 value 四捨五入後的寫法。"""
    if abs(target - value) <= max(abs(target) * tolerance, 0.01):
        return True
    return round(value) == target or round(value, 1) == target


def _pair_results(a: float, b: float, mode: str) -> Iterator[float]:
    """a、b 兩個數字能算出的簡單結果。``mode`` 決定收哪些運算：

    - ``percent``：只算 a 的 b%（月薪 × 回答自己寫的 50%）
    - ``scale``：再加乘除（金額 ÷ 價格 = 買幾顆）
    - ``all``：再加加減、a 佔 b 的百分比、a 相對 b 的漲跌幅（房租 ÷ 月薪 = 佔比）
    """
    if 0 < b <= 100:
        yield a * b / 100
    if 0 < a <= 100:
        yield a * b / 100
    if mode == "percent":
        return
    yield a * b
    if b:
        yield abs(a / b)
    if a:
        yield abs(b / a)
    if mode == "scale":
        return
    yield a + b
    yield abs(a - b)
    if b:
        yield abs(a / b * 100)
        yield abs((a - b) / b * 100)
    if a:
        yield abs(b / a * 100)
        yield abs((b - a) / a * 100)


def _is_derivable_from_context(
    target: float,
    context_numbers: list[float],
    tool_numbers: list[float],
    response_percents: list[float],
    tolerance: float = _CONTEXT_DERIVATION_TOLERANCE,
) -> bool:
    """target 是否為「使用者的數字」與其他數字的簡單運算結果。

    只收至少有一個運算元是使用者數字的組合（工具數字之間的運算由
    ``_is_simple_derivation`` 管），而且依對象限縮運算：
    - 使用者 × 使用者：全部簡單運算（房租 ÷ 月薪 = 佔比、月薪 − 房租）
    - 使用者 × 回答自己寫的百分比：只算「a 的 b%」（月薪 × 50% = 必要支出）
    - 使用者 × 工具數字：只算乘除與百分比（金額 ÷ 價格 = 買幾顆、金額 × 漲幅%）
    """
    target = abs(target)
    for i, a in enumerate(context_numbers):
        pairings = (
            [(b, "all") for j, b in enumerate(context_numbers) if j != i]
            + [(p, "percent") for p in response_percents]
            + [(t, "scale") for t in tool_numbers]
        )
        for b, mode in pairings:
            if any(_matches(target, r, tolerance) for r in _pair_results(a, b, mode)):
                return True
    return False


def _allocation_indices(numbers: list[ExtractedNumber]) -> set[int]:
    """回答裡「加起來剛好 100%」的百分比（資產配置、預算分配）的索引。

    「BTC 60%、ETH 30%、現金 10%」是模型的建議，不是它宣稱的市場數據，
    沒有任何工具輸出能「來源」它。只認加總 100 的組合，單獨一個「漲 85%」照樣要查。
    """
    pcts = [
        (i, n.value)
        for i, n in enumerate(numbers)
        if n.raw.rstrip().endswith("%") and 0 < n.value <= 100
    ][:12]
    found: set[int] = set()
    for size in range(2, min(len(pcts), 8) + 1):
        for combo in combinations(pcts, size):
            if abs(sum(v for _, v in combo) - 100) <= 0.5:
                found.update(i for i, _ in combo)
    return found


@dataclass
class VerificationResult:
    """數字一致性檢查結果。"""

    passed: bool  # True = 無可疑幻覺
    suspicious_numbers: list[ExtractedNumber] = field(default_factory=list)
    total_checked: int = 0
    total_suspicious: int = 0

    @property
    def has_hallucination_risk(self) -> bool:
        """是否有幻覺風險（可疑數字 > 0）。"""
        return len(self.suspicious_numbers) > 0


# 比對成本上限：工具輸出（K 線等）可能有上千個數字，兩兩配對會爆
_TOOL_NUMBER_LIMIT = 200

# 千分位數字（64,000）。extract_all_numbers 的 \d+ 會把它拆成 64 與 000，這裡補整數值
_THOUSANDS_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")


def _collect_tool_numbers(tool_outputs: list[str]) -> list[float]:
    """合併所有市場資料工具輸出的數字（一般＋金融＋千分位＋數量級換算），去重。"""
    numbers: dict[float, None] = {}
    for out in tool_outputs:
        if not isinstance(out, str):
            continue
        for num in extract_all_numbers(out):
            numbers[num] = None
        for m in _THOUSANDS_RE.finditer(out):
            numbers[float(m.group().replace(",", ""))] = None
        for fin_num in extract_financial_numbers(out):
            numbers[fin_num.value] = None
            scale = _unit_scale(fin_num.raw)
            if scale != 1.0:
                numbers[fin_num.value * scale] = None
    return list(numbers)


def verify_numeric_consistency(
    response: str,
    tool_outputs: list[str],
    skip_small_numbers: bool = True,
    user_context: str = "",
) -> VerificationResult:
    """檢查 response 中的金融數字是否都能在來源中找到。

    來源 = 市場資料工具的輸出 ∪ 使用者自己講過的數字（``user_context``）及其簡單運算。

    Args:
        response: LLM 的最終回應。
        tool_outputs: 本次執行**市場資料類**工具輸出的文字清單
            （``extract_tool_outputs_from_messages(..., market_only=True)``）。
            空清單＝沒有可對照的市場資料（例如只用了 resolve_symbol），直接放行。
        skip_small_numbers: 跳過 < 1 的數字（如 RSI 0.5、機率 0.95），
            這些通常不是金融價格/點數。預設 True。
        user_context: 使用者本輪問題與對話歷史。裡面出現過的數字（含「6 萬＝60000」
            換算）不算幻覺；回答裡由它們算出來的佔比、百分比分配、加減乘除也不算。

    Returns:
        VerificationResult：通過 / 可疑數字清單。

    Examples:
        >>> result = verify_numeric_consistency(
        ...     "BTC $64000，盤中波動 660 點",
        ...     ["price: 64000, change: -0.30"],
        ... )
        >>> result.has_hallucination_risk
        True  # 660 在 tool 輸出中找不到
    """
    response_numbers = extract_financial_numbers(response)

    if not response_numbers or not tool_outputs:
        return VerificationResult(passed=True, total_checked=0)

    tool_numbers = _collect_tool_numbers(tool_outputs)
    context_numbers = extract_context_numbers(user_context)
    allocation = _allocation_indices(response_numbers)
    response_percents = [
        n.value
        for n in response_numbers
        if n.raw.rstrip().endswith("%") and 0 < n.value <= 100
    ]

    suspicious: list[ExtractedNumber] = []
    for idx, num in enumerate(response_numbers):
        if skip_small_numbers and num.value < 1:
            continue
        if idx in allocation:
            continue

        # 「6 萬」同時是 6 與 60000，兩種寫法有一種有來源就算
        scale = _unit_scale(num.raw)
        candidates = [num.value] + ([num.value * scale] if scale != 1.0 else [])
        if any(
            _is_sourced(c, tool_numbers, context_numbers, response_percents)
            for c in candidates
        ):
            continue
        suspicious.append(num)

    return VerificationResult(
        passed=len(suspicious) == 0,
        suspicious_numbers=suspicious,
        total_checked=len(response_numbers),
        total_suspicious=len(suspicious),
    )


def _is_sourced(
    value: float,
    tool_numbers: list[float],
    context_numbers: list[float],
    response_percents: list[float],
) -> bool:
    """value 能由工具輸出或使用者的數字得出（直接出現／簡單運算）。"""
    if any(abs(value - c) <= max(abs(c) * 0.02, 0.01) for c in context_numbers):
        return True
    if _is_simple_derivation(value, tool_numbers):
        return True
    return bool(context_numbers) and _is_derivable_from_context(
        value, context_numbers, tool_numbers[:_TOOL_NUMBER_LIMIT], response_percents
    )


# ============================================================================
# 3. 從 messages 中抽取 tool outputs
# ============================================================================


def merge_used_tools_from_messages(used_tools: list, messages: list) -> list:
    """從最終 messages 的 ToolMessage 補齊 used_tools（就地追加並回傳）。

    2026-09-12 DeepSeek 實測：``stream_mode=["values","messages"]`` 沒把
    ToolMessage 當 chunk 送出來，靠串流收集的 used_tools 整輪是空的——RunMetrics
    的 tool_calls 有值、tool_outputs 也有，只有這個清單空。可觀測性不該依賴
    provider 的串流形狀，最終 messages 才是事實。
    """
    if not messages:
        return used_tools
    from langchain_core.messages import ToolMessage

    for msg in messages:
        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", None) or "tool"
            if name not in used_tools:
                used_tools.append(name)
    return used_tools


# 輸出不是「回答裡數字的來源」的工具：symbol 解析、時間錨點、skill／知識載入、記憶、
# 澄清、帳本／行事曆／交易日誌（使用者自己的紀錄，不是市場資料）。
# 驗證只對照市場資料類工具的輸出——用過這些工具就開始比對，個人理財答案會整批被標可疑。
# 白名單不可行（工具與 MCP 會一直長）：沒列在這裡的工具一律當資料來源，漏列只會多驗證一點。
NON_MARKET_TOOLS = frozenset(
    {
        "resolve_symbol",
        "get_current_time_taipei",
        "get_current_time_tool",
        "introduction_tool",
        "request_more_tools",
        "load_skill",
        "load_knowledge",
        "list_my_skills_memory",
        "propose_custom_skill",
        "remember",
        "clarify",
        "query_ledger",
        "record_entry",
        "update_ledger_entry",
        "delete_ledger_entry",
        "add_calendar_event",
        "list_calendar_events",
        "record_call",
        "get_my_scorecard",
        "submit_kyc_application",
    }
)


def extract_tool_outputs_from_messages(
    messages: list, market_only: bool = False
) -> list[str]:
    """從 LangGraph messages 中抽出所有 ToolMessage 的 content。

    Args:
        messages: LangGraph state 中的 messages list。
        market_only: True 時略過 ``NON_MARKET_TOOLS`` 的輸出，只留市場資料類工具
            （供 verify_numeric_consistency 當驗證來源）。名稱未知的工具當資料來源。

    Returns:
        ToolMessage content 字串清單。
    """
    outputs: list[str] = []
    if not messages:
        return outputs

    from langchain_core.messages import ToolMessage

    for msg in messages:
        if isinstance(msg, ToolMessage):
            if market_only and getattr(msg, "name", None) in NON_MARKET_TOOLS:
                continue
            content = msg.content
            if isinstance(content, str):
                outputs.append(content)
            elif isinstance(content, list):
                # 部分 provider 用 list of dict
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text") or part.get("content") or ""
                        if isinstance(text, str):
                            outputs.append(text)
                    elif isinstance(part, str):
                        outputs.append(part)
            elif isinstance(content, dict):
                # dict 形式（如 JSON tool output）
                import json

                try:
                    outputs.append(json.dumps(content, ensure_ascii=False))
                except (TypeError, ValueError):
                    outputs.append(str(content))
    return outputs


__all__ = [
    "ExtractedNumber",
    "NON_MARKET_TOOLS",
    "VerificationResult",
    "extract_financial_numbers",
    "extract_all_numbers",
    "extract_context_numbers",
    "verify_numeric_consistency",
    "extract_tool_outputs_from_messages",
]
