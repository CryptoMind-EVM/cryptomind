"""Tests for log_run_metrics（P3 SLO 指標）。

設計契約：
- 每輪對話落一行可解析的結構化指標，讓「p95 TTFT 是多少」「快速通道命中率
  多少」變成可回答的問題（此前只有前端那顆「耗時 79.5 秒」badge）
- route 區分 fast_path / claw_loop，用來算命中率
- ttft_s 可為 None（快速通道一次回完，沒有串流）
- 指標紀錄絕不可影響主流程——任何例外都要被吞掉
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from core.agents.manager.claw_loop import _ToolStepTracker, log_run_metrics

pytestmark = pytest.mark.unit


class TestToolStepTrackerTotal:
    def test_starts_at_zero(self):
        assert _ToolStepTracker().total == 0

    def test_counts_each_start(self):
        t = _ToolStepTracker()
        t.start("get_price")
        t.start("get_news")
        assert t.total == 2

    def test_end_does_not_change_total(self):
        t = _ToolStepTracker()
        t.start("get_price")
        t.end("get_price")
        assert t.total == 1

    def test_repeated_tool_still_counts_each_call(self):
        """同一個工具被呼叫兩次要算兩次——這正是要監控的浪費模式。"""
        t = _ToolStepTracker()
        t.start("get_price")
        t.start("get_price")
        assert t.total == 2


class TestLogRunMetrics:
    def test_emits_one_parseable_line(self, caplog):
        with caplog.at_level(logging.INFO):
            log_run_metrics(
                route="claw_loop",
                elapsed_s=79.5,
                ttft_s=47.3,
                tool_calls=3,
                response_chars=1200,
            )
        line = "\n".join(caplog.messages)
        assert "[RunMetrics]" in line
        assert "route=claw_loop" in line
        assert "elapsed_s=79.50" in line
        assert "ttft_s=47.30" in line
        assert "tool_calls=3" in line
        assert "resp_chars=1200" in line

    def test_missing_ttft_renders_as_dash(self, caplog):
        """快速通道一次回完沒有串流，TTFT 不存在——不可印成 0（會汙染 p95）。"""
        with caplog.at_level(logging.INFO):
            log_run_metrics(route="fast_path", elapsed_s=0.83, response_chars=42)
        line = "\n".join(caplog.messages)
        assert "ttft_s=-" in line
        assert "ttft_s=0" not in line

    def test_route_distinguishes_fast_path(self, caplog):
        with caplog.at_level(logging.INFO):
            log_run_metrics(route="fast_path", elapsed_s=0.5)
            log_run_metrics(route="claw_loop", elapsed_s=50.0)
        joined = "\n".join(caplog.messages)
        assert "route=fast_path" in joined
        assert "route=claw_loop" in joined

    def test_never_raises(self):
        """指標紀錄失敗不可影響主對話。"""
        # 傳不可格式化的型別，確認被內部吞掉
        log_run_metrics(route="x", elapsed_s="not-a-number")  # type: ignore[arg-type]
        log_run_metrics(route="x", elapsed_s=1.0, ttft_s="bad")  # type: ignore[arg-type]


class TestStep1ExtensionFields:
    """Model Mixer Step 1 行尾擴充：model/provider/preset_id/agents/tokens。"""

    def test_emits_extension_fields(self, caplog):
        with caplog.at_level(logging.INFO):
            log_run_metrics(
                route="claw_loop",
                elapsed_s=50.0,
                model="gpt-5.4",
                provider="openai",
                preset_id="prst_abc",
                agents="general_research",
                prompt_tokens=1200,
                completion_tokens=340,
            )
        line = "\n".join(caplog.messages)
        assert "model=gpt-5.4" in line
        assert "provider=openai" in line
        assert "preset_id=prst_abc" in line
        assert "agents=general_research" in line
        assert "prompt_tokens=1200" in line
        assert "completion_tokens=340" in line

    def test_missing_extension_fields_render_as_dash(self, caplog):
        """0 token 是「沒記到」不是「零成本」——缺漏一律 '-'，不可汙染基準線。"""
        with caplog.at_level(logging.INFO):
            log_run_metrics(route="fast_path", elapsed_s=0.5)
        line = "\n".join(caplog.messages)
        assert "model=-" in line
        assert "provider=-" in line
        assert "preset_id=-" in line
        assert "agents=-" in line
        assert "prompt_tokens=-" in line
        assert "completion_tokens=-" in line
        assert "prompt_tokens=0" not in line

    def test_multi_agent_preset_joined_in_agents(self, caplog):
        with caplog.at_level(logging.INFO):
            log_run_metrics(
                route="claw_loop",
                elapsed_s=1.0,
                agents="general_research,finance_markets",
            )
        line = "\n".join(caplog.messages)
        assert "agents=general_research,finance_markets" in line


class TestRunMetricsExtras:
    """_run_metrics_extras：從 state／token tracker 組擴充欄位。"""

    def _tracker_with(self, prompt, completion):
        from core.agents.token_tracker import TokenTracker, TokenUsage

        tracker = TokenTracker()
        tracker.record(
            TokenUsage(
                model="m",
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )
        )
        return tracker

    def test_reads_state_and_tracker_delta(self):
        from core.agents.manager.claw_loop import _run_metrics_extras

        manager = SimpleNamespace(_token_tracker=self._tracker_with(100, 40))
        state = {
            "llm_model": "gpt-5.4",
            "llm_provider": "openai",
            "preset_id": "prst_abc",
            "preset_config": {"agent_ids": ["general_research", "finance_markets"]},
        }
        extras = _run_metrics_extras(state, manager, token_baseline=0)
        assert extras["model"] == "gpt-5.4"
        assert extras["provider"] == "openai"
        assert extras["preset_id"] == "prst_abc"
        assert extras["agents"] == "general_research,finance_markets"
        assert extras["prompt_tokens"] == 100
        assert extras["completion_tokens"] == 40

    def test_baseline_excludes_earlier_usage(self):
        """manager 跨請求快取、tracker 累計——只算 baseline 之後的增量。"""
        from core.agents.manager.claw_loop import _run_metrics_extras
        from core.agents.token_tracker import TokenUsage

        tracker = self._tracker_with(100, 40)
        tracker.record(
            TokenUsage(model="m", prompt_tokens=7, completion_tokens=3, total_tokens=10)
        )
        manager = SimpleNamespace(_token_tracker=tracker)
        extras = _run_metrics_extras({}, manager, token_baseline=1)
        assert extras["prompt_tokens"] == 7
        assert extras["completion_tokens"] == 3

    def test_missing_tracker_and_state_yield_nones(self):
        from core.agents.manager.claw_loop import _run_metrics_extras

        extras = _run_metrics_extras({}, SimpleNamespace(), 0)
        assert extras["model"] is None
        assert extras["preset_id"] is None
        assert extras["agents"] is None
        assert extras["prompt_tokens"] is None
        assert extras["completion_tokens"] is None

    def test_never_raises_on_garbage(self):
        from core.agents.manager.claw_loop import _run_metrics_extras

        assert _run_metrics_extras(None, None, 0) == {}


class TestRouterReasonField:
    """2026-09-05 線上 P0：#644 給 RouteDecision 加了 reason，但
    log_run_metrics 沒這個參數——fan-out 路徑在彙整完成後的 metrics 收尾
    TypeError，整個 run 被標成 error（答案其實已經送到使用者眼前）。
    參數綁定階段就炸，函式內「任何例外都要吞掉」的 try/except 來不及吞。
    """

    def test_typed_dict_matches_what_metrics_dict_actually_returns(self):
        """RouteMetrics 的欄位必須跟實際回傳的鍵一模一樣。

        TypedDict 漂掉不會有 runtime 錯誤——它只影響 mypy，而這個專案的 mypy
        沒進 CI，所以漂了不會有人發現，靜態保護就靜默失效。下面那個 runtime
        契約測試擋的是「metrics_dict 加了鍵但 log_run_metrics 沒加參數」；
        這個擋的是「TypedDict 沒跟上 metrics_dict」。兩層漏洞不同。

        實測過 mypy 認得這個契約：多一個欄位而 log_run_metrics 沒跟上，
        會報 ``Extra argument "..." from **args for "log_run_metrics"``。
        """
        from core.agents.router import RouteDecision, RouteMetrics

        decision = RouteDecision(
            domains=("crypto",), depth="research", gate=None,
            source="router", reason=None,
        )
        assert set(RouteMetrics.__annotations__) == set(decision.metrics_dict()), (
            "RouteMetrics 與 metrics_dict() 的鍵不一致——靜態檢查會看著錯的契約"
        )

    def test_metrics_dict_keys_are_all_accepted_kwargs(self):
        """契約：metrics_dict() 的每個鍵都必須是 log_run_metrics 的參數。

        呼叫端（claw_loop 三處）都用 ``**metrics_dict()`` 展開——router
        新增欄位而 metrics 沒跟上時，這裡先紅，而不是線上每次分析收尾都炸。
        """
        import inspect

        from core.agents.router import DEPTH_RESEARCH, RouteDecision

        decision = RouteDecision(
            domains=("crypto",),
            depth=DEPTH_RESEARCH,
            gate=None,
            source="fallback",
            reason="timeout",
        )
        accepted = set(inspect.signature(log_run_metrics).parameters)
        missing = set(decision.metrics_dict()) - accepted
        assert not missing, (
            f"RouteDecision.metrics_dict() 產生的鍵 log_run_metrics 不收：{missing}。"
            "呼叫端以 **展開，少一個參數就 TypeError。"
        )

    def test_reason_rendered_in_line(self, caplog):
        """reason 要印出來——source=fallback 時，timeout 與 API 錯誤在
        log 上一模一樣，但只有前者調 ROUTER_TIMEOUT_SECONDS 有用。"""
        with caplog.at_level(logging.INFO):
            log_run_metrics(
                route="claw_loop", elapsed_s=41.2, source="fallback", reason="timeout"
            )
        line = "\n".join(caplog.messages)
        assert "reason=timeout" in line
