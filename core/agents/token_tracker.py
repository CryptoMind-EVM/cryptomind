"""Token usage tracking for monitoring and debugging.

Records token counts per request. In a BYOK model, users pay their own
provider, so cost estimation is not needed here.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TokenUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: float = field(default_factory=time.time)


class TokenTracker:
    """Tracks token usage history for monitoring and debugging."""

    MAX_HISTORY = 500
    _DEFAULT_BUDGET_USD = 10.0

    def __init__(self, max_budget_usd: float = _DEFAULT_BUDGET_USD) -> None:
        self._usage_history: List[TokenUsage] = []
        self._max_budget = max_budget_usd

    def record(self, usage: TokenUsage) -> None:
        self._usage_history.append(usage)
        if len(self._usage_history) > self.MAX_HISTORY:
            self._usage_history = self._usage_history[-self.MAX_HISTORY :]

    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self._usage_history)

    def usage_since(self, baseline_count: int) -> dict:
        """回傳自 ``baseline_count`` 之後新增的 token 用量摘要。

        用於把「本次 graph 執行」的 token 增量接給 TraceCollector——
        ``_token_tracker`` 是跨請求累計的，呼叫端在 node 開始前先記下
        ``len(history)`` 作為 baseline，結束時呼叫本方法即可得到本次增量。

        Args:
            baseline_count: node 開始前的 ``total_requests()`` 值。

        Returns:
            ``{"prompt_tokens", "completion_tokens", "total_tokens"}`` 摘要；
            若無新增用量，三項皆為 0。命名對齊 ``TraceCollector`` 期待的金鑰。
        """
        recent = self._usage_history[baseline_count:]
        return {
            "prompt_tokens": sum(u.prompt_tokens for u in recent),
            "completion_tokens": sum(u.completion_tokens for u in recent),
            "total_tokens": sum(u.total_tokens for u in recent),
        }

    def total_requests(self) -> int:
        return len(self._usage_history)

    @property
    def last_usage(self) -> Optional[TokenUsage]:
        return self._usage_history[-1] if self._usage_history else None

    # -- Legacy methods (backward-compatible with llm.py logging) --

    def total_cost(self) -> float:
        """Legacy: returns 0.0. Cost tracking removed in BYOK model."""
        return 0.0

    def is_over_budget(self) -> bool:
        """Legacy: always returns False. Budget enforcement removed."""
        return False
