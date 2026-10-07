"""每一種 fail-closed 降級都要說得出原因（2026-09-05）。

route_query 對逾時與 API 錯誤都回同一個 fallback，metrics 只有
source="fallback"——線上看到它時無法判斷「調大 ROUTER_TIMEOUT_SECONDS」
有沒有用：對逾時有用，對 429／認證失敗完全沒用。

#629 在 eval harness 修過同一個問題（整份報表失敗卻印出像樣的準確率），
production 這條路沒修到。
"""

from __future__ import annotations

import asyncio

import pytest

from core.agents.router import route_query

pytestmark = pytest.mark.unit

# has_finance_signal 認的是代號／幣別（BTC、2330、ETH），不是「本益比」這種詞——
# 「台積電本益比多少」實測 False。挑一題真的會 veto 的。
_Q = "BTC 現在多少"                # 有金融訊號 → 硬否決，連 Router 都不跑
_ROUTED = "幫我看看這個平台安全嗎"  # 走 router 路徑


def _run(coro):
    return asyncio.run(coro)


class TestFallbackReasonIsSpecific:
    def test_timeout_says_timeout(self):
        async def slow(_prompt):
            await asyncio.sleep(5)
            return "{}"

        d = _run(route_query(_ROUTED, slow, timeout_s=0.05))
        assert d.source == "fallback"
        assert d.reason == "timeout", "逾時必須可辨識——這是唯一調大秒數有用的情況"

    def test_api_error_carries_the_exception_type(self):
        class RateLimited(Exception):
            pass

        async def boom(_prompt):
            raise RateLimited("429")

        d = _run(route_query(_ROUTED, boom, timeout_s=4))
        assert d.source == "fallback"
        assert d.reason == "invoke_error:RateLimited", (
            "API 錯誤要帶型別，否則 429／認證失敗／連線中斷分不出來"
        )

    def test_unparseable_reply_is_not_confused_with_timeout(self):
        async def garbage(_prompt):
            return "抱歉我不確定"

        d = _run(route_query(_ROUTED, garbage, timeout_s=4))
        assert d.source == "fallback"
        assert d.reason == "no_json", "模型回非 JSON ≠ 逾時，調秒數沒用"

    def test_bad_depth_is_distinguishable(self):
        async def wrong(_prompt):
            return '{"depth":"deep","domains":[],"gate":null}'

        d = _run(route_query(_ROUTED, wrong, timeout_s=4))
        assert d.reason == "bad_depth"


class TestReasonReachesMetrics:
    def test_metrics_dict_carries_reason(self):
        async def boom(_prompt):
            raise RuntimeError("x")

        d = _run(route_query(_ROUTED, boom, timeout_s=4))
        assert d.metrics_dict().get("reason") == "invoke_error:RuntimeError", (
            "沒進 metrics 就等於沒有——線上看的是 RunMetrics 那行"
        )

    def test_successful_route_has_no_reason(self):
        async def ok(_prompt):
            return '{"depth":"lookup","domains":[],"gate":null}'

        d = _run(route_query(_ROUTED, ok, timeout_s=4))
        assert d.source == "router"
        assert d.metrics_dict().get("reason") is None


class TestZeroCostPathsUnaffected:
    """反向守衛：不碰 LLM 的兩條路不該有 reason，也不該變成 fallback。"""

    def test_smalltalk_still_t0_cache(self):
        async def never(_prompt):
            raise AssertionError("寒暄不該打 LLM")

        d = _run(route_query("你好", never, timeout_s=4))
        assert d.source == "t0_cache" and d.reason is None

    def test_finance_signal_still_veto(self):
        async def never(_prompt):
            raise AssertionError("金融訊號硬否決不該打 LLM")

        d = _run(route_query(_Q, never, timeout_s=4))
        assert d.source == "veto" and d.reason is None
