"""admin「Agent 運行」指標（tier 3 觀測項）：滾動存放、聚合、端點、接線。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.agents import run_metrics_store as rms

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _row(**kw):
    base = {
        "ts": time.time(),
        "route": "claw_loop",
        "elapsed_s": 10.0,
        "tool_calls": 2,
        "prompt_tokens": 100,
        "completion_tokens": 50,
    }
    base.update(kw)
    return base


class TestSummarize:
    def test_routes_latency_and_fast_path_rate(self):
        rows = [
            _row(route="fast_path_router", elapsed_s=1.0, tool_calls=0),
            _row(route="fast_path_t0", elapsed_s=1.2, tool_calls=0),
            _row(
                route="claw_loop",
                elapsed_s=8.0,
                source="veto",
                depth="research",
                skills="tw-stock-technical",
            ),
            _row(
                route="claw_loop",
                elapsed_s=20.0,
                source="router",
                depth="lookup",
                skills="tw-stock-technical,crypto-technical-analysis",
                reason="timeout",
            ),
            _row(route="fanout", elapsed_s=40.0, tool_calls=6),
        ]
        s = rms.summarize(rows, hours=24, now=time.time())
        assert s["runs"] == 5 and s["fast_path_rate"] == 0.4
        assert s["routes"]["claw_loop"] == 2 and s["routes"]["fanout"] == 1
        assert s["latency_by_route"]["claw_loop"] == {
            "n": 2,
            "p50": 8.0,
            "p95": 20.0,
            "mean": 14.0,
        }
        assert s["sources"] == {"-": 3, "veto": 1, "router": 1}
        assert s["top_skills"]["tw-stock-technical"] == 2
        assert s["prompt_tokens"] == 500 and s["completion_tokens"] == 250
        assert s["fallback_reasons"] == {"timeout": 1}
        assert s["tool_calls_mean"] == 2.0

    def test_window_cutoff_and_empty(self):
        now = time.time()
        rows = [_row(ts=now - 30 * 3600), _row(ts=now - 60)]
        s = rms.summarize(rows, hours=24, now=now)
        assert s["runs"] == 1
        empty = rms.summarize([], hours=24, now=now)
        assert (
            empty["runs"] == 0
            and empty["fast_path_rate"] is None
            and empty["latency_by_route"] == {}
        )

    def test_dash_placeholders_are_ignored(self):
        s = rms.summarize(
            [_row(prompt_tokens="-", completion_tokens="-", elapsed_s="-")], hours=24
        )
        assert (
            s["prompt_tokens"] == 0
            and s["latency_by_route"]["claw_loop"]["p50"] is None
        )


class TestRecord:
    def test_fallback_deque_when_no_redis(self):
        rms._FALLBACK.clear()
        with patch.object(rms, "_redis", return_value=None):
            rms.record({"route": "claw_loop", "elapsed_s": 3.3, "bogus": "dropped"})
            rows = rms._load()
        assert (
            len(rows) == 1
            and rows[0]["route"] == "claw_loop"
            and "bogus" not in rows[0]
        )
        assert rows[0]["ts"] > 0
        rms._FALLBACK.clear()

    def test_record_never_raises(self):
        class _Boom:
            def pipeline(self):
                raise RuntimeError("redis down")

        with patch.object(rms, "_redis", return_value=_Boom()):
            rms.record({"route": "claw_loop"})  # 不得丟例外

    def test_redis_path_uses_lpush_and_ltrim(self):
        calls = []

        class _Pipe:
            def lpush(self, key, payload):
                calls.append(("lpush", key, json.loads(payload)["route"]))

            def ltrim(self, key, start, end):
                calls.append(("ltrim", key, start, end))

            def execute(self):
                calls.append(("execute",))

        class _Client:
            def pipeline(self):
                return _Pipe()

        with patch.object(rms, "_redis", return_value=_Client()):
            rms.record({"route": "fanout"})
        assert calls == [
            ("lpush", rms.REDIS_KEY, "fanout"),
            ("ltrim", rms.REDIS_KEY, 0, rms.MAX_ROWS - 1),
            ("execute",),
        ]


class TestEndpoint:
    def _client(self):
        from api.routers.admin import stats as stats_module

        app = FastAPI()
        app.include_router(stats_module.router)

        async def fake_admin():
            return {"user_id": "admin", "role": "admin"}

        app.dependency_overrides[stats_module.require_admin] = fake_admin
        return TestClient(app)

    def test_returns_runs_and_brief(self):
        with (
            patch(
                "core.agents.run_metrics_store.summary",
                return_value={"hours": 24.0, "runs": 3},
            ),
            patch("core.daily_brief.store.admin_stats", return_value={"sent_today": 2}),
        ):
            res = self._client().get("/stats/agent-runs?hours=24")
        assert res.status_code == 200
        assert (
            res.json()["runs"]["runs"] == 3 and res.json()["brief"]["sent_today"] == 2
        )

    def test_brief_failure_does_not_break_panel(self):
        with (
            patch("core.agents.run_metrics_store.summary", return_value={"runs": 0}),
            patch("core.daily_brief.store.admin_stats", side_effect=RuntimeError("db")),
        ):
            res = self._client().get("/stats/agent-runs")
        assert res.status_code == 200 and res.json()["brief"] == {
            "error": "unavailable"
        }


class TestWiring:
    def test_run_metrics_hook_and_frontend(self):
        claw = (REPO / "core" / "agents" / "manager" / "claw_loop.py").read_text(
            encoding="utf-8"
        )
        assert "run_metrics_store.record(" in claw
        js = (REPO / "web" / "js" / "admin-stats.js").read_text(encoding="utf-8")
        assert "/api/admin/stats/agent-runs" in js and "this.loadAgentRuns()" in js
        assert 'id="stats-agent-runs"' in js

    @pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
    def test_admin_i18n_keys(self, lang):
        d = json.loads(
            (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
        )["admin"]
        for key in ("agentRunsTitle", "agentRunsRuns", "briefTitle", "briefSendsToday"):
            assert key in d, f"{lang} 缺 admin.{key}"
