"""Mixer 的可設定性與失敗可見性——把「寫死的預設」與「靜默失敗」釘住。

三件事的共同教訓：預設值可以有主張，但不能替使用者關上門；而任何
fail-closed 的降級，都必須在報表／log 上看得出來是降級。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class TestRouterTimeoutIsConfigurable:
    """4 秒是「中階 mini」的預設，不是對可用模型的限制。"""

    @staticmethod
    def _reload(monkeypatch, value):
        import importlib

        import core.agents.router as router

        if value is None:
            monkeypatch.delenv("ROUTER_TIMEOUT_SECONDS", raising=False)
        else:
            monkeypatch.setenv("ROUTER_TIMEOUT_SECONDS", value)
        return importlib.reload(router)

    def test_default_is_four_seconds(self, monkeypatch):
        assert self._reload(monkeypatch, None).ROUTER_TIMEOUT_SECONDS == 4.0

    def test_env_raises_budget_for_slow_models(self, monkeypatch):
        """BYOK 想用 reasoning 模型當 router 是使用者的選擇，不是我們的。"""
        assert self._reload(monkeypatch, "45").ROUTER_TIMEOUT_SECONDS == 45.0

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-3"])
    def test_invalid_or_nonpositive_falls_back_to_default(self, monkeypatch, bad):
        """0／負值會讓 wait_for 立刻逾時＝router 永遠不生效（靜默全滅）。"""
        assert self._reload(monkeypatch, bad).ROUTER_TIMEOUT_SECONDS == 4.0

    def teardown_method(self):
        import importlib

        import core.agents.router as router

        importlib.reload(router)


class TestLoopForkFlag:
    """Step 6 的軟門檻原本沒有開關，deploy 完就對所有使用者生效。"""

    def test_disabled_by_default_watchdog_never_arms(self, monkeypatch):
        monkeypatch.delenv("MIXER_LOOP_FORK_ENABLED", raising=False)
        from core.agents.manager.claw_loop import _fork_initial_state

        assert _fork_initial_state()["used"] is True, "flag off 必須永不武裝"

    def test_enabled_arms_the_watchdog(self, monkeypatch):
        monkeypatch.setenv("MIXER_LOOP_FORK_ENABLED", "1")
        from core.agents.manager.claw_loop import (
            _FORK_HARD_STEPS,
            _FORK_SOFT_STEPS,
            _fork_initial_state,
        )

        state = _fork_initial_state()
        assert state["used"] is False
        assert state["soft"] == _FORK_SOFT_STEPS
        assert state["hard"] == _FORK_HARD_STEPS


class TestEvalHarnessKnobs:
    """用 120 tokens 評測 reasoning 模型，量到的是截斷不是準確率。"""

    def _payload(self, **kwargs):
        import httpx

        from scripts.eval_router import build_live_invoke

        captured = {}

        class _FakeClient:
            def __init__(self, *a, **kw):
                captured["timeout"] = kw.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, _url, headers=None, json=None):
                captured["json"] = json
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "{}"}}]},
                    request=httpx.Request("POST", "https://x/y"),
                )

        invoke = build_live_invoke("https://x", "m", "k", **kwargs)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(httpx, "AsyncClient", _FakeClient)
            asyncio.run(invoke("p"))
        return captured

    def test_max_tokens_and_timeout_are_honoured(self):
        cap = self._payload(max_tokens=4096, http_timeout=90.0)
        assert cap["json"]["max_tokens"] == 4096
        assert cap["timeout"] == 90.0

    def test_thinking_toggle_is_opt_in(self):
        assert "chat_template_kwargs" not in self._payload()["json"]
        cap = self._payload(no_thinking=True)
        assert cap["json"]["chat_template_kwargs"] == {"thinking": False}


class TestFailedRunIsNotReportedAsAccuracy:
    """整份失敗的跑不可以印出看似正常的準確率。"""

    def test_report_flags_invalid_run(self):
        from scripts.eval_router import render_router_report

        scored = {
            "total": {"n": 10, "depth_ok": 5, "domains_ok": 5, "gate_ok": 9,
                      "dangerous": 0},
            "per_source": {},
            "misses": [],
        }
        stats = {"calls": 10, "failures": 10, "last_error": "HTTPStatusError: 500"}
        report = render_router_report("live x/y", scored, 1.0, stats)
        assert "無效" in report and "10/10" in report

    def test_clean_run_has_no_warning(self):
        from scripts.eval_router import render_router_report

        scored = {
            "total": {"n": 10, "depth_ok": 10, "domains_ok": 10, "gate_ok": 10,
                      "dangerous": 0},
            "per_source": {},
            "misses": [],
        }
        report = render_router_report(
            "live x/y", scored, 1.0, {"calls": 10, "failures": 0, "last_error": ""}
        )
        assert "無效" not in report


class TestEvalPacing:
    """--delay 的接線（2026-09-04：旗標加了、run_router 簽名沒改，12 項測試全綠）。"""

    def test_run_router_accepts_delay(self):
        import inspect

        from scripts.eval_router import run_router

        assert "delay_s" in inspect.signature(run_router).parameters, (
            "CLI 有 --delay 但 run_router 收不到——跑起來才會 TypeError"
        )

    def test_cli_wires_delay_through(self):
        """CLI 旗標必須真的傳到 run_router，不是宣告完就沒下文。"""
        src = (Path(__file__).resolve().parent.parent / "scripts/eval_router.py").read_text(
            encoding="utf-8"
        )
        assert '"--delay"' in src
        assert "delay_s=args.delay" in src

    def test_delay_actually_paces_the_calls(self):
        """真的跑一遍：兩題、間隔 0.2 秒，總耗時必須 >= 0.2。"""
        import asyncio
        import time as _time

        from scripts.eval_router import run_router

        entries = [
            {"id": "a", "query": "hi", "expected": {}},
            {"id": "b", "query": "hello", "expected": {}},
        ]

        async def _fake(_prompt):
            return '{"depth":"lookup","domains":[],"gate":null}'

        started = _time.monotonic()
        asyncio.run(run_router(entries, lambda _e: _fake, delay_s=0.2))
        assert _time.monotonic() - started >= 0.2

    def test_zero_delay_does_not_sleep(self):
        """預設 0＝不節流，別讓每個 CI 跑都平白多等。"""
        import asyncio
        import time as _time

        from scripts.eval_router import run_router

        entries = [{"id": str(i), "query": "hi", "expected": {}} for i in range(5)]

        async def _fake(_prompt):
            return '{"depth":"lookup","domains":[],"gate":null}'

        started = _time.monotonic()
        asyncio.run(run_router(entries, lambda _e: _fake))
        assert _time.monotonic() - started < 1.0


class TestHarnessMatchesProduction:
    """harness 必須能重現 production，否則量到的是自己的設定。

    2026-09-05：build_live_invoke 原本一律把 max_tokens 塞進 payload，但
    production（core/agents/manager/llm.py 的 _create_model_instance）只設
    {"model", "temperature"}，從不限制輸出長度。結果 reasoning 模型的思考
    吃掉額度、JSON 被截斷 → 32 題判成 no_json，那是**測試設定造成的假失敗**。

    實測差距（DeepSeek v4 flash，同一組 131 題）：
      max_tokens=200 → router n=40、fallback 33（全是截斷）
      max_tokens=0   → router n=64、fallback  9
    """

    def test_zero_omits_the_field_entirely(self):
        from scripts.eval_router import build_live_invoke

        sent = {}

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"choices": [{"message": {"content": "{}"}}]}

        class _Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, _url, headers=None, json=None):
                sent.update(json or {})
                return _Resp()

        from unittest.mock import patch

        import httpx

        inv = build_live_invoke("https://x.test", "m", "k", max_tokens=0)
        with patch.object(httpx, "AsyncClient", _Client):
            asyncio.run(inv("p"))
        assert "max_tokens" not in sent, (
            "max_tokens=0 必須不送此欄位——送了就無法重現 production"
        )

    def test_positive_value_still_sent(self):
        """反向守衛：明確給值時仍要送，否則 --max-tokens 這個旗標就沒作用了。"""
        from unittest.mock import patch

        import httpx

        from scripts.eval_router import build_live_invoke

        sent = {}

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"choices": [{"message": {"content": "{}"}}]}

        class _Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, _url, headers=None, json=None):
                sent.update(json or {})
                return _Resp()

        inv = build_live_invoke("https://x.test", "m", "k", max_tokens=250)
        with patch.object(httpx, "AsyncClient", _Client):
            asyncio.run(inv("p"))
        assert sent.get("max_tokens") == 250
